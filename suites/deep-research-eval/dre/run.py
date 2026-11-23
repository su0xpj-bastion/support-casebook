"""python -m dre run --system mock|perplexity|openai|anthropic [--limit N] [--repeats K] [--faults f] [--update-baseline]"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from .grader import grade
from .systems import AnthropicSystem, MockSystem, OpenAISystem, PerplexitySystem

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "dsqa_slice.jsonl"
GATES = {"format_min": 0.95, "citations_min": 0.90, "list_precision_min": 0.80, "recall_drop_max": 0.10,
         "consistency_min": 0.80, "nogo_p": 0.01, "go_p": 0.10}
# USD per million tokens, from each provider's pricing page; None = not recorded, cost not computed.
PRICES = {"mock": (1.0, 4.0), "perplexity": (None, None), "openai": (2.0, 10.0), "anthropic": (4.0, 20.0)}
KEYS = {"perplexity": "PERPLEXITY_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def load(limit: int | None = None) -> list[dict]:
    with DATA.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return rows[:limit] if limit else rows


def drop_p_value(base_ok: int, base_n: int, cand_ok: int, cand_n: int) -> float:
    """One-sided two-proportion z-test: P(candidate this much worse by chance)."""
    p1, p2 = base_ok / base_n, cand_ok / cand_n
    pooled = (base_ok + cand_ok) / (base_n + cand_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / base_n + 1 / cand_n)) or 1e-9
    z = (p1 - p2) / se
    return 0.5 * math.erfc(z / math.sqrt(2))


def wilson(ok: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = ok / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - m), min(1.0, c + m)


def evaluate(system, items: list[dict], repeats: int = 1, workers: int = 4) -> list[dict]:
    def one(job):
        item, k = job
        try:
            ans = system.ask(item, k)
        except Exception as exc:                       # a failed request is a scored failure, not a crash
            from .systems import Answer
            ans = Answer("", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        g = grade(ans.text, item["answer"], item["answer_type"])
        return {"id": item["id"], "attempt": k, "category": item["category"], "answer_type": item["answer_type"],
                "answer": asdict(ans), "grade": asdict(g)}
    jobs = [(it, k) for k in range(repeats) for it in items]
    with cf.ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, jobs))


def summarize(rows: list[dict], system: str) -> dict:
    first = [r for r in rows if r["attempt"] == 0]
    n = len(first)
    singles = [r for r in first if r["answer_type"].startswith("Single")]
    sets = [r for r in first if not r["answer_type"].startswith("Single")]
    ok = sum(r["grade"]["correct"] for r in first)
    lo, hi = wilson(ok, n)
    price = PRICES.get(system, (None, None))
    tin = sum(r["answer"]["input_tokens"] for r in first)
    tout = sum(r["answer"]["output_tokens"] for r in first)
    cost = None if None in price else (tin * price[0] + tout * price[1]) / 1e6
    by_cat = defaultdict(lambda: [0, 0])
    for r in first:
        by_cat[r["category"]][0] += r["grade"]["correct"]
        by_cat[r["category"]][1] += 1
    consistency = None
    if any(r["attempt"] > 0 for r in rows):
        by_id = defaultdict(set)
        for r in rows:
            by_id[r["id"]].add(r["grade"]["correct"])
        consistency = sum(1 for v in by_id.values() if len(v) == 1) / len(by_id)
    return {
        "n": n, "correct": ok, "accuracy": ok / n, "accuracy_ci95": [lo, hi],
        "single_accuracy": sum(r["grade"]["correct"] for r in singles) / len(singles) if singles else None,
        "list_f1": statistics.mean(r["grade"]["f1"] for r in sets) if sets else None,
        "list_precision": statistics.mean(r["grade"]["precision"] for r in sets) if sets else None,
        "list_recall": statistics.mean(r["grade"]["recall"] for r in sets) if sets else None,
        "format_rate": sum(r["grade"]["has_final"] for r in first) / n,
        "citation_rate": sum(bool(r["answer"]["citations"]) for r in first) / n,
        "errors": sum(bool(r["answer"]["error"]) for r in first),
        "failure_kinds": dict(Counter(r["grade"]["kind"] for r in first)),
        "latency_p50_s": statistics.median(r["answer"]["seconds"] for r in first),
        "mean_searches": statistics.mean(r["answer"]["searches"] for r in first),
        "cost_usd": cost, "cost_per_correct_usd": (cost / ok) if cost is not None and ok else None,
        "by_category": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_cat.items())},
        "consistency": consistency,
    }


def checks(s: dict, base: dict | None) -> list[dict]:
    out = []

    def add(name, ok, detail, gated=True):
        out.append({"check": name, "status": "pass" if ok else ("fail" if gated else "warn"), "detail": detail})

    add("format_compliance", s["format_rate"] >= GATES["format_min"],
        f"{s['format_rate']:.0%} of answers end with a FINAL ANSWER line (floor {GATES['format_min']:.0%})")
    add("citations_present", s["citation_rate"] >= GATES["citations_min"],
        f"{s['citation_rate']:.0%} of answers cite at least one source (floor {GATES['citations_min']:.0%})")
    if s["list_precision"] is not None:
        add("list_precision", s["list_precision"] >= GATES["list_precision_min"],
            f"mean precision of list answers {s['list_precision']:.2f}: wrong items mixed into lists (floor {GATES['list_precision_min']})")
    if base:
        p = drop_p_value(base["correct"], base["n"], s["correct"], s["n"])
        verdict = "NO-GO" if p < GATES["nogo_p"] else ("GO" if p >= GATES["go_p"] else "INCONCLUSIVE")
        out.append({"check": "accuracy_vs_baseline", "status": {"GO": "pass", "INCONCLUSIVE": "warn", "NO-GO": "fail"}[verdict],
                    "detail": f"{s['correct']}/{s['n']} vs baseline {base['correct']}/{base['n']}: one-sided p={p:.3f} -> {verdict}"})
        if s["list_recall"] is not None and base.get("list_recall") is not None:
            drop = base["list_recall"] - s["list_recall"]
            add("list_recall_vs_baseline", drop <= GATES["recall_drop_max"],
                f"list recall {s['list_recall']:.2f} vs baseline {base['list_recall']:.2f} (max drop {GATES['recall_drop_max']})")
    if s["consistency"] is not None:
        add("consistency", s["consistency"] >= GATES["consistency_min"],
            f"{s['consistency']:.0%} of questions get the same verdict on every repeat (floor {GATES['consistency_min']:.0%})")
    return out


def render(system: str, s: dict, cs: list[dict], meta: dict) -> str:
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    f = lambda v, fmt: "n/a" if v is None else format(v, fmt)
    lines = [f"# deep-research-eval: {system}", "",
             f"{s['n']} DeepSearchQA questions · {meta['repeats']} repeat(s) · {meta['seconds']:.1f} s"
             + (f" · faults `{meta['faults']}`" if meta.get("faults") else ""), "",
             "| Metric | Value |", "|---|---|",
             f"| Fully correct | **{s['correct']}/{s['n']} = {s['accuracy']:.0%}** (95% CI {s['accuracy_ci95'][0]:.0%}–{s['accuracy_ci95'][1]:.0%}) |",
             f"| Single-answer accuracy | {f(s['single_accuracy'], '.0%')} |",
             f"| List answers: F1 / precision / recall | {f(s['list_f1'], '.2f')} / {f(s['list_precision'], '.2f')} / {f(s['list_recall'], '.2f')} |",
             f"| Failure kinds | {', '.join(f'{k} {v}' for k, v in sorted(s['failure_kinds'].items()))} |",
             f"| FINAL ANSWER format / citations | {s['format_rate']:.0%} / {s['citation_rate']:.0%} |",
             f"| Latency p50 / searches per question | {s['latency_p50_s']:.1f} s / {s['mean_searches']:.1f} |",
             f"| Cost / per correct answer | {('$' + format(s['cost_usd'], '.3f')) if s['cost_usd'] is not None else 'n/a'} / "
             f"{('$' + format(s['cost_per_correct_usd'], '.4f')) if s['cost_per_correct_usd'] is not None else 'n/a'} |"]
    if s["consistency"] is not None:
        lines.append(f"| Consistency across repeats | {s['consistency']:.0%} |")
    lines += ["", "| | Check | Detail |", "|---|---|---|"] + [f"| {icon[c['status']]} | {c['check']} | {c['detail']} |" for c in cs]
    lines += ["", f"**Verdict: {meta['verdict']}**", ""]
    return "\n".join(lines)


def build(name: str, faults: set[str]):
    if name == "mock":
        return MockSystem(faults)
    key = os.environ.get(KEYS[name], "")
    if not key:
        raise SystemExit(f"[ERROR] {KEYS[name]} is not set")
    return {"perplexity": PerplexitySystem, "openai": OpenAISystem, "anthropic": AnthropicSystem}[name](key)


def main() -> int:
    ap = argparse.ArgumentParser(prog="dre")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--system", default="mock", choices=["mock", "perplexity", "openai", "anthropic"])
    r.add_argument("--limit", type=int, default=None, help="first N questions of the slice")
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--faults", default="")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--update-baseline", action="store_true")
    r.add_argument("--out", default="reports/latest")
    a = ap.parse_args()

    faults = {f for f in a.faults.split(",") if f}
    system = build(a.system, faults)
    items = load(a.limit)
    start = time.perf_counter()
    rows = evaluate(system, items, a.repeats, a.workers)
    s = summarize(rows, a.system)
    base_path = ROOT / "baselines" / f"{a.system}.json"
    if a.update_baseline:
        base_path.parent.mkdir(exist_ok=True)
        base_path.write_text(json.dumps(s, indent=2) + "\n")
    base = json.loads(base_path.read_text()) if base_path.exists() and not a.update_baseline else None
    cs = checks(s, base)
    verdict = "FAIL" if any(c["status"] == "fail" for c in cs) else "PASS"
    meta = {"system": a.system, "repeats": a.repeats, "faults": a.faults, "seconds": time.perf_counter() - start,
            "verdict": verdict, "baseline": str(base_path.relative_to(ROOT)) if base else None}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render(a.system, s, cs, meta)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": s, "checks": cs, "rows": rows}, indent=2,
                                                 ensure_ascii=False), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    print(md)
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
