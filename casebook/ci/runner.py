"""`python -m casebook ci test`: the CI test step.

1. Choose what to run: everything, a change-based selection (--base), and/or one shard (--shard i/n).
2. Run pytest with the history plugin (attempt 1).
3. Rerun only the failed tests, up to --reruns times (attempts 2..). A test that passes on a rerun
   of the same commit is flaky; one that fails every time is a real failure.
4. Classify every failure with casebook.ci.signatures.
5. Exit 0 only if every failure was flaky or quarantined. Flaky passes are reported, not hidden:
   `ci quarantine update` turns them into quarantine entries.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from casebook.ci import quarantine as Q
from casebook.ci import select as S
from casebook.ci import shard as SH
from casebook.ci import signatures as SG

ROOT = Path(__file__).resolve().parents[2]


def targets(base: str | None, shard: str | None, durations: dict[str, float]) -> tuple[list[str], dict[str, Any]]:
    info: dict[str, Any] = {}
    args = ["tests"]
    if base:
        sel = S.select(S.changed_files(base))
        args = sel.pytest_args()
        info["selection"] = {"run_all": sel.run_all, "files": sorted(sel.files), "nodes": len(sel.nodes),
                             "reasons": {k: v[:3] for k, v in list(sel.reasons.items())[:50]}}
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        files = SH.test_files() if args == ["tests"] else sorted({a.split("::")[0] for a in args})
        plan = SH.plan(files, n, durations)
        mine = set(plan[i]["files"])
        args = [a for a in (args if args != ["tests"] else files) if a.split("::")[0] in mine]
        info["shard"] = {"index": i, "of": n, "files": sorted(mine), "planned_seconds": plan[i]["seconds"]}
    return args, info


def _pytest(args: list[str], history: Path, run_id: str, attempt: int) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--ci-history={history}",
           f"--ci-run-id={run_id}", f"--ci-attempt={attempt}", *args]
    return subprocess.run(cmd, cwd=ROOT).returncode


def run(*, base: str | None = None, shard: str | None = None, reruns: int = 2,
        history: Path = ROOT / ".ci" / "history.jsonl", report: Path | None = None) -> int:
    args, info = targets(base, shard, SH.load_durations())
    if not args:
        print("ci: nothing to test for this change")
        _write(report, {**info, "ran": 0, "failures": []})
        return 0
    run_id = uuid.uuid4().hex[:12]
    code = _pytest(args, history, run_id, 1)
    rows = [r for r in Q.load_history(history) if r["run"] == run_id]
    failed = sorted({r["nodeid"] for r in rows if r["outcome"] == "failed"})
    quarantined = Q.active_nodeids()
    still = [f for f in failed if f not in quarantined]
    attempts: dict[str, list[str]] = {f: ["failed"] for f in still}
    for attempt in range(2, reruns + 2):
        if not still:
            break
        _pytest(still, history, run_id, attempt)
        rows = [r for r in Q.load_history(history) if r["run"] == run_id]
        latest = {r["nodeid"]: r["outcome"] for r in rows if r["attempt"] == attempt}
        for f in still:
            attempts[f].append(latest.get(f, "missing"))
        still = [f for f in still if latest.get(f) != "passed"]
    messages = {r["nodeid"]: r["message"] for r in rows if r["outcome"] == "failed"}
    failures = []
    for f in failed:
        sig = SG.classify(messages.get(f, ""))
        kind = "quarantined" if f in quarantined else ("flaky" if f not in still else "failed")
        failures.append({"nodeid": f, "result": kind, "attempts": attempts.get(f, ["failed"]),
                         "signature": sig.id, "class": sig.cls, "fix": sig.fix})
    for x in failures:
        print(f"ci: {x['result']:11} {x['signature']} {x['class']:11} {x['nodeid']}")
    out = 1 if still else (0 if code in (0, 1, 5) else code)
    _write(report, {**info, "run": run_id, "ran": len({r['nodeid'] for r in rows}), "failures": failures,
                    "exit": out})
    return out


def _write(path: Path | None, data: dict[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
