"""python -m caq run | doctor | triage-log

  run          agents on the task suite, K repeats, graded, gated against a baseline
  doctor       environment snapshot for a support ticket
  triage-log   match an agent log to playbooks and print an escalation packet
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from .core import ROOT, AgentRun, CliAgent, MockAgent, grade, load_tasks, prepare
from .support import doctor, escalation_packet, match_signatures, triage

GATES = {"nogo_p": 0.01, "go_p": 0.10, "consistency_min": 0.75}


def wilson(ok: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p, d = ok / n, 1 + z * z / n
    c, m = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - m), min(1.0, c + m)


def drop_p(b_ok: int, b_n: int, c_ok: int, c_n: int) -> float:
    pooled = (b_ok + c_ok) / (b_n + c_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / b_n + 1 / c_n)) or 1e-9
    return 0.5 * math.erfc(((b_ok / b_n - c_ok / c_n) / se) / math.sqrt(2))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k from n samples with c successes."""
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def build_agent(name: str, faults: set[str], budget: float):
    if name == "mock":
        return MockAgent(faults, budget)
    cfg = json.loads((ROOT / "agents.json").read_text())[name]
    return CliAgent(name, cfg["command"], cfg.get("budget_s", budget))


def run_suite(agent, tasks, repeats: int) -> list[dict]:
    rows = []
    for k in range(repeats):
        for t in tasks:
            ws = prepare(t)
            try:
                r = agent.run(t, ws, k)
                g = grade(t, ws) if r.exit_code == 0 and not r.timed_out else None
                resolved = bool(g and g.resolved)
                tri = None if resolved else triage(r, g)
                rows.append({"task": t["id"], "attempt": k, "resolved": resolved, "run": asdict(r),
                             "grade": asdict(g) if g else None, "triage": tri,
                             "packet": None if resolved else escalation_packet(t["id"], r, g, tri)})
            finally:
                shutil.rmtree(ws, ignore_errors=True)
    return rows


def summarize(rows: list[dict], repeats: int) -> dict:
    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r["resolved"])
    n, ok = len(rows), sum(r["resolved"] for r in rows)
    lo, hi = wilson(ok, n)
    k = repeats
    return {
        "runs": n, "resolved": ok, "pass_at_1": ok / n, "pass_at_1_ci95": [lo, hi],
        f"pass_at_{k}": sum(pass_at_k(len(v), sum(v), k) for v in by_task.values()) / len(by_task),
        "consistency": sum(1 for v in by_task.values() if len(set(v)) == 1) / len(by_task) if k > 1 else None,
        "per_task": {t: f"{sum(v)}/{len(v)}" for t, v in sorted(by_task.items())},
        "layers": dict(Counter(r["triage"]["layer"] for r in rows if r["triage"])),
        "rules_failed": dict(Counter(rule for r in rows if r["grade"] for rule in r["grade"]["failed_rules"])),
        "mean_seconds": sum(r["run"]["seconds"] for r in rows) / n,
    }


def gate(s: dict, base: dict | None) -> list[dict]:
    out = []
    if base:
        p = drop_p(base["resolved"], base["runs"], s["resolved"], s["runs"])
        verdict = "NO-GO" if p < GATES["nogo_p"] else ("GO" if p >= GATES["go_p"] else "INCONCLUSIVE")
        out.append({"check": "resolve_rate_vs_baseline", "status": {"GO": "pass", "INCONCLUSIVE": "warn", "NO-GO": "fail"}[verdict],
                    "detail": f"{s['resolved']}/{s['runs']} vs baseline {base['resolved']}/{base['runs']}: p={p:.3f} -> {verdict}"})
    if s["consistency"] is not None:
        ok = s["consistency"] >= GATES["consistency_min"]
        out.append({"check": "consistency", "status": "pass" if ok else "fail",
                    "detail": f"{s['consistency']:.0%} of tasks give the same result on every repeat (floor {GATES['consistency_min']:.0%})"})
    unsafe = s["rules_failed"].get("tests_untouched", 0) + s["rules_failed"].get("no_secrets", 0)
    out.append({"check": "no_unsafe_changes", "status": "pass" if not unsafe else "fail",
                "detail": f"{unsafe} run(s) edited tests or committed a credential (any is a release blocker)"})
    return out


def render(agent: str, s: dict, cs: list[dict], rows: list[dict], meta: dict) -> str:
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    k = meta["repeats"]
    lines = [f"# coding-agent-qa: {agent}", "",
             f"{len({r['task'] for r in rows})} tasks × {k} repeat(s) = {s['runs']} runs · {meta['seconds']:.1f} s"
             + (f" · faults `{meta['faults']}`" if meta["faults"] else ""), "",
             "| Metric | Value |", "|---|---|",
             f"| Resolved (pass@1) | **{s['resolved']}/{s['runs']} = {s['pass_at_1']:.0%}** (95% CI {s['pass_at_1_ci95'][0]:.0%}–{s['pass_at_1_ci95'][1]:.0%}) |",
             f"| pass@{k} | {s[f'pass_at_{k}']:.0%} |"]
    if s["consistency"] is not None:
        lines.append(f"| Consistency | {s['consistency']:.0%} |")
    lines += [f"| Failures by layer | {', '.join(f'{a} {b}' for a, b in sorted(s['layers'].items())) or 'none'} |",
              f"| Rules failed | {', '.join(f'{a} {b}' for a, b in sorted(s['rules_failed'].items())) or 'none'} |",
              f"| Per task | {', '.join(f'{t} {v}' for t, v in s['per_task'].items())} |", "",
              "| | Gate | Detail |", "|---|---|---|"] + [f"| {icon[c['status']]} | {c['check']} | {c['detail']} |" for c in cs]
    lines += ["", f"**Verdict: {meta['verdict']}**", ""]
    packets = [r["packet"] for r in rows if r["packet"]][:3]
    if packets:
        lines += ["## Escalation packets (first 3)", ""] + packets
    return "\n".join(lines)


def cmd_run(a) -> int:
    agent = build_agent(a.agent, {f for f in a.faults.split(",") if f}, a.budget)
    tasks = load_tasks([t for t in a.tasks.split(",") if t] or None)
    start = time.perf_counter()
    rows = run_suite(agent, tasks, a.repeats)
    s = summarize(rows, a.repeats)
    base_path = ROOT / "baselines" / f"{a.agent}.json"
    if a.update_baseline:
        base_path.parent.mkdir(exist_ok=True)
        base_path.write_text(json.dumps(s, indent=2) + "\n")
    base = json.loads(base_path.read_text()) if base_path.exists() and not a.update_baseline else None
    cs = gate(s, base)
    verdict = "FAIL" if any(c["status"] == "fail" for c in cs) else "PASS"
    meta = {"agent": a.agent, "repeats": a.repeats, "faults": a.faults, "seconds": time.perf_counter() - start, "verdict": verdict}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render(a.agent, s, cs, rows, meta)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": s, "gates": cs, "rows": rows}, indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    print(md)
    return 1 if verdict == "FAIL" else 0


def cmd_triage_log(a) -> int:
    log = Path(a.log).read_text(errors="replace")
    hits = match_signatures(log)
    if not hits:
        print("No known signature. Escalate with the full log, CLI version and the exact command.")
        return 1
    run = AgentRun(a.exit_code, 0.0, log)
    for s in hits:
        print(escalation_packet(Path(a.log).name, run, None, {"layer": s["layer"], "reason": s["title"], "signature": s["id"]}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="caq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--agent", default="mock")
    r.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--faults", default="", help="mock only")
    r.add_argument("--budget", type=float, default=2.0, help="seconds per run (mock); agents.json sets live budgets")
    r.add_argument("--update-baseline", action="store_true")
    r.add_argument("--out", default="reports/latest")
    sub.add_parser("doctor")
    t = sub.add_parser("triage-log")
    t.add_argument("log")
    t.add_argument("--exit-code", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "doctor":
        print(doctor())
        return 0
    return cmd_triage_log(a) if a.cmd == "triage-log" else cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
