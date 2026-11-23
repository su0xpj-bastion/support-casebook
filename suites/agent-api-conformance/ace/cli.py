"""python -m ace run [--target mock|live] [--providers a,b] [--only s1,s2] [--faults f] [--out DIR]"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .adapters import ADAPTERS
from .scenarios import SCENARIOS, Outcome, run

ROOT = Path(__file__).resolve().parent.parent
ICON = {"pass": "✅", "fail": "❌", "skip": "⏭️", "info": "ℹ️"}


def cost(outcomes: list[Outcome], price: dict) -> float | None:
    if price.get("input") is None or price.get("output") is None:
        return None
    total = 0.0
    for o in outcomes:
        for r in o.results:
            if r.input_tokens is None or r.output_tokens is None:
                continue
            cached = r.cached_tokens if price.get("cached_input") is not None else 0
            write = r.cache_write_tokens if price.get("cache_write") is not None else 0
            uncached = r.input_tokens - cached - write
            total += (uncached * price["input"] + cached * (price.get("cached_input") or 0)
                      + write * (price.get("cache_write") or 0) + r.output_tokens * price["output"]) / 1e6
    return total


def render(table: dict[str, list[Outcome]], meta: dict) -> str:
    names = list(table)
    lines = [f"# agent-api-conformance: {meta['target']}", "",
             f"{len(names)} provider(s) · {meta['seconds']:.1f} s · verdict **{meta['verdict']}**", "",
             "| Scenario | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for fn in SCENARIOS:
        row = []
        for n in names:
            o = next((o for o in table[n] if o.scenario == fn.__name__), None)
            row.append(f"{ICON[o.status]}" if o else "")
        if any(row):
            lines.append(f"| {fn.__name__} | " + " | ".join(row) + " |")
    lines.append("| **cost of this run** | " + " | ".join(
        "n/a" if meta["cost"][n] is None else f"${meta['cost'][n]:.4f}" for n in names) + " |")
    lines += ["", "## Details", ""]
    for n in names:
        lines.append(f"### {n} (`{meta['providers'][n]['protocol']}`, `{meta['providers'][n]['model']}`)")
        for o in table[n]:
            lines.append(f"- {ICON[o.status]} **{o.scenario}**: {o.detail}" + ("" if o.gated else " *(not gated)*"))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="ace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--target", choices=["mock", "live"], default="mock")
    r.add_argument("--providers", default="", help="comma-separated names from providers.json")
    r.add_argument("--only", default="", help="comma-separated scenario names")
    r.add_argument("--faults", default="", help="mock only")
    r.add_argument("--out", default="reports/latest")
    args = ap.parse_args()

    config = json.loads((ROOT / "providers.json").read_text())[args.target]
    wanted = [p for p in args.providers.split(",") if p] or list(config)
    server = None
    if args.target == "mock":
        from mockserve.server import serve
        server = serve(0, {f for f in args.faults.split(",") if f})
    table, used, prices = {}, {}, {}
    start = time.perf_counter()
    for name in wanted:
        p = config[name]
        key = "test-key" if args.target == "mock" else os.environ.get(p["key_env"], "")
        if not key:
            print(f"[SKIP] {name}: {p['key_env']} is not set", file=sys.stderr)
            continue
        base = p["base_url"].replace("MOCK", f"http://127.0.0.1:{server.server_port}") if server else p["base_url"]
        adapter = ADAPTERS[p["protocol"]](name, p["model"], base, key, p.get("cfg", {}))
        table[name] = run(adapter, {s for s in args.only.split(",") if s} or None, set(p.get("skip", [])))
        used[name], prices[name] = {"protocol": p["protocol"], "model": p["model"]}, p.get("price", {})
    if server:
        server.shutdown()
    if not table:
        print("[ERROR] no provider ran (set API keys for --target live)", file=sys.stderr)
        return 2
    failed = any(o.status == "fail" and o.gated for outs in table.values() for o in outs)
    meta = {"target": args.target, "seconds": time.perf_counter() - start, "verdict": "FAIL" if failed else "PASS",
            "providers": used, "cost": {n: cost(table[n], prices[n]) for n in table}, "faults": args.faults}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render(table, meta)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.json").write_text(json.dumps({"meta": meta, "results": {
        n: [{"scenario": o.scenario, "status": o.status, "detail": o.detail, "gated": o.gated,
             "calls": [r.__dict__ | {"tool_calls": [c.__dict__ for c in r.tool_calls]} for r in o.results]}
            for o in outs] for n, outs in table.items()}}, indent=2, default=str), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    print(md)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
