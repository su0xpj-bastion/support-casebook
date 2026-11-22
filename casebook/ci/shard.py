"""Split the suite into N shards of similar wall time, using recorded durations.

Shards are whole test files, not single tests: several files build an expensive module-scoped
fixture (tests/test_packs.py runs every error pack once), and splitting such a file would run that
fixture in every shard. Assignment is longest-processing-time-first: sort files by duration and
give each to the shard with the least time so far.

Durations come from the CI history (casebook.ci.pytest_plugin); ci/durations.json holds the
committed per-file means so a shard plan doesn't need the history file.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DURATIONS = ROOT / "ci" / "durations.json"


def file_durations(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Mean wall time per test file, from first-attempt runs. A run's file time = sum of its tests."""
    per_run: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if r.get("attempt", 1) == 1:
            per_run[(r["run"], r["nodeid"].split("::")[0])] += r["duration"]
    by_file: dict[str, list[float]] = defaultdict(list)
    for (_run, f), d in per_run.items():
        by_file[f].append(d)
    return {f: round(sum(v) / len(v), 2) for f, v in sorted(by_file.items())}


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_durations(d: dict[str, float], path: Path = DURATIONS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plan(files: list[str], n: int, durations: dict[str, float]) -> list[dict[str, Any]]:
    """Assign files to n shards. Unknown files get the median known duration."""
    if n < 1:
        raise ValueError("need at least one shard")
    known = sorted(durations.values())
    default = known[len(known) // 2] if known else 1.0
    weighted = sorted(((durations.get(f, default), f) for f in files), key=lambda x: (-x[0], x[1]))
    shards: list[dict[str, Any]] = [{"index": i, "files": [], "seconds": 0.0} for i in range(n)]
    for secs, f in weighted:
        target = min(shards, key=lambda s: (s["seconds"], s["index"]))
        target["files"].append(f)
        target["seconds"] = round(target["seconds"] + secs, 2)
    for s in shards:
        s["files"].sort()
    return shards


def test_files() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py"))
