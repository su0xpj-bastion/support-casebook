"""Flake detection from test history, and the quarantine list.

A test is **flaky** when it both passed and failed on the same commit (same sha): the code didn't
change, the result did. Retrying the same commit is the only evidence used; a test that fails on
every attempt is a real failure, never a flake.

Policy (QuarantinePolicy):
- quarantine a test after `min_flaky_commits` commits on which it flipped;
- release it after `release_after` consecutive passing runs recorded since it was quarantined.

The list lives in ci/quarantine.json and is committed, so quarantining is a reviewed change.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
QUARANTINE = ROOT / "ci" / "quarantine.json"


@dataclass(frozen=True)
class QuarantinePolicy:
    min_flaky_commits: int = 1
    release_after: int = 30


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load(path: Path = QUARANTINE) -> dict[str, Any]:
    if not path.exists():
        return {"tests": []}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save(data: dict[str, Any], path: Path = QUARANTINE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["tests"] = sorted(data["tests"], key=lambda t: t["nodeid"])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def active_nodeids(path: Path = QUARANTINE) -> set[str]:
    return {t["nodeid"] for t in load(path)["tests"]}


def analyse(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per test: runs, failures, commits where it flipped, flake rate, last message."""
    by_test: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["outcome"] in ("passed", "failed"):
            by_test[r["nodeid"]].append(r)
    out: dict[str, dict[str, Any]] = {}
    for nid, rs in by_test.items():
        by_sha: dict[str, set[str]] = defaultdict(set)
        for r in rs:
            by_sha[r["sha"]].add(r["outcome"])
        flipped = sorted(sha for sha, outs in by_sha.items() if outs == {"passed", "failed"})
        fails = [r for r in rs if r["outcome"] == "failed"]
        out[nid] = {
            "runs": len(rs),
            "failures": len(fails),
            "flaky_commits": flipped,
            "consistent_fail_commits": sorted(sha for sha, outs in by_sha.items() if outs == {"failed"}),
            "flake_rate": round(len([r for r in fails if r["sha"] in flipped]) / len(rs), 4),
            "last_message": fails[-1]["message"][-300:] if fails else "",
        }
    return out


def _passes_since(rows: list[dict[str, Any]], nodeid: str, since_ts: float) -> int:
    """Consecutive passes at the end of the test's history, counting only runs after `since_ts`."""
    n = 0
    for r in reversed([r for r in rows if r["nodeid"] == nodeid and r.get("ts", 0) >= since_ts]):
        if r["outcome"] == "passed":
            n += 1
        elif r["outcome"] == "failed":
            break
    return n


def update(rows: list[dict[str, Any]], current: dict[str, Any], policy: QuarantinePolicy = QuarantinePolicy(),
           now: float = 0.0) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return (new quarantine data, added nodeids, released nodeids)."""
    stats = analyse(rows)
    tests = {t["nodeid"]: t for t in current.get("tests", [])}
    added, released = [], []
    for nid, s in stats.items():
        if nid not in tests and len(s["flaky_commits"]) >= policy.min_flaky_commits:
            tests[nid] = {"nodeid": nid, "since": now, "reason": "passed and failed on the same commit",
                          "flaky_commits": s["flaky_commits"], "flake_rate": s["flake_rate"],
                          "last_message": s["last_message"]}
            added.append(nid)
    for nid, t in list(tests.items()):
        if nid in added:
            continue
        if _passes_since(rows, nid, t.get("since", 0.0)) >= policy.release_after:
            del tests[nid]
            released.append(nid)
    return {"policy": {"min_flaky_commits": policy.min_flaky_commits, "release_after": policy.release_after},
            "tests": list(tests.values())}, added, released
