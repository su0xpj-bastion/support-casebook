"""`python -m casebook ci <command>`."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from casebook.ci import quarantine as Q
from casebook.ci import runner
from casebook.ci import select as S
from casebook.ci import shard as SH
from casebook.ci import signatures as SG

HISTORY = runner.ROOT / ".ci" / "history.jsonl"


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    ci = sub.add_parser("ci", help="CI tooling for this repo's own test suite")
    cs = ci.add_subparsers(dest="ci_cmd", required=True)

    s = cs.add_parser("select", help="tests affected by the change since BASE")
    s.add_argument("--base", default="origin/main")
    s.add_argument("--head", default="HEAD")
    s.add_argument("--explain", action="store_true")
    s.set_defaults(fn=_select)

    t = cs.add_parser("test", help="run tests with history, reruns of failures, and failure signatures")
    t.add_argument("--base", help="only tests affected by the change since BASE")
    t.add_argument("--shard", help="i/n: run shard i of n (0-based)")
    t.add_argument("--reruns", type=int, default=2)
    t.add_argument("--history", default=str(HISTORY))
    t.add_argument("--report")
    t.set_defaults(fn=lambda a: runner.run(base=a.base, shard=a.shard, reruns=a.reruns, history=Path(a.history),
                                           report=Path(a.report) if a.report else None))

    sh = cs.add_parser("shard", help="print the shard plan, or update durations from history")
    sh.add_argument("-n", type=int, default=3)
    sh.add_argument("--update-durations", action="store_true")
    sh.add_argument("--history", default=str(HISTORY))
    sh.add_argument("--matrix", action="store_true", help="print a GitHub Actions matrix JSON")
    sh.set_defaults(fn=_shard)

    q = cs.add_parser("quarantine", help="show, or update from history")
    q.add_argument("action", choices=["show", "update"])
    q.add_argument("--history", default=str(HISTORY))
    q.set_defaults(fn=_quarantine)

    c = cs.add_parser("classify", help="classify failure text (file or stdin) with the signature table")
    c.add_argument("file", nargs="?")
    c.set_defaults(fn=_classify)


def _select(a: argparse.Namespace) -> int:
    sel = S.select(S.changed_files(a.base, a.head))
    if a.explain:
        print(json.dumps({"run_all": sel.run_all, "reasons": sel.reasons}, indent=2))
    else:
        print(" ".join(sel.pytest_args()))
    return 0


def _shard(a: argparse.Namespace) -> int:
    if a.update_durations:
        d = SH.file_durations(Q.load_history(Path(a.history)))
        SH.save_durations(d)
        print(f"{len(d)} files -> {SH.DURATIONS.relative_to(runner.ROOT)}")
        return 0
    plan = SH.plan(SH.test_files(), a.n, SH.load_durations())
    if a.matrix:
        print(json.dumps({"shard": [f"{s['index']}/{a.n}" for s in plan]}))
    else:
        for s in plan:
            print(f"shard {s['index']}/{a.n}  {s['seconds']:7.1f}s  {' '.join(s['files'])}")
    return 0


def _quarantine(a: argparse.Namespace) -> int:
    cur = Q.load()
    if a.action == "update":
        new, added, released = Q.update(Q.load_history(Path(a.history)), cur, now=time.time())
        Q.save(new)
        print(f"added {added}, released {released}")
        cur = new
    for t in cur["tests"]:
        print(f"{t['nodeid']}  flake_rate={t.get('flake_rate')}  commits={len(t.get('flaky_commits', []))}")
    if not cur["tests"]:
        print("no quarantined tests")
    return 0


def _classify(a: argparse.Namespace) -> int:
    text = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
    print(json.dumps(SG.classify(text).as_dict(), indent=2))
    return 0
