"""pytest plugin: record every test result to a history file and apply the quarantine list.

    pytest --ci-history .ci/history.jsonl          # append one row per test
    pytest --ci-no-quarantine                       # treat quarantined tests like any other

History row: {"run", "sha", "attempt", "nodeid", "outcome", "duration", "message"}.
`duration` is setup + call + teardown, so a module fixture's setup cost lands on the first test
of the module (that's what sharding needs).

A quarantined test still runs. If it fails, the failure is reported as xfail ("quarantined: ...")
so it doesn't fail the build, and the history still records it as "failed".
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from casebook.ci import quarantine as Q

_STATE: dict[str, Any] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("casebook-ci")
    g.addoption("--ci-history", default=None, help="append test results to this JSONL file")
    g.addoption("--ci-attempt", type=int, default=1, help="attempt number recorded in history (reruns use 2, 3, ...)")
    g.addoption("--ci-run-id", default=None, help="run id recorded in history (default: random)")
    g.addoption("--ci-no-quarantine", action="store_true", help="ignore ci/quarantine.json")
    g.addoption("--ci-quarantine-file", default=str(Q.QUARANTINE), help="quarantine list to apply")


def _sha(root: Path) -> str:
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def pytest_configure(config: pytest.Config) -> None:
    _STATE.clear()
    _STATE["durations"] = {}
    _STATE["failed"] = {}
    _STATE["quarantined"] = set() if config.getoption("--ci-no-quarantine") \
        else Q.active_nodeids(Path(config.getoption("--ci-quarantine-file")))
    _STATE["quarantine_hits"] = []
    path = config.getoption("--ci-history")
    _STATE["history"] = Path(path) if path else None
    _STATE["run"] = config.getoption("--ci-run-id") or uuid.uuid4().hex[:12]
    _STATE["attempt"] = config.getoption("--ci-attempt")
    _STATE["sha"] = _sha(config.rootpath)
    _STATE["rows"] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # type: ignore[no-untyped-def]
    outcome = yield
    rep = outcome.get_result()
    nid = item.nodeid
    _STATE["durations"][nid] = _STATE["durations"].get(nid, 0.0) + rep.duration
    if rep.failed:
        _STATE["failed"][nid] = (rep.longreprtext or "")[-2000:]
        if nid in _STATE["quarantined"] and rep.when == "call":
            rep.outcome = "skipped"
            rep.wasxfail = "quarantined: see ci/quarantine.json"
            _STATE["quarantine_hits"].append(nid)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    if nodeid in _STATE["failed"]:
        status = "failed"
    else:
        status = _STATE.get("skipped", {}).get(nodeid, "passed")
    _STATE["rows"].append({
        "run": _STATE["run"], "sha": _STATE["sha"], "attempt": _STATE["attempt"], "nodeid": nodeid,
        "outcome": status, "duration": round(_STATE["durations"].get(nodeid, 0.0), 4),
        "message": _STATE["failed"].get(nodeid, "")[-600:], "ts": round(time.time(), 1),
    })


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and not getattr(report, "wasxfail", None):
        _STATE.setdefault("skipped", {})[report.nodeid] = "skipped"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    path = _STATE.get("history")
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in _STATE["rows"]:
            fh.write(json.dumps(row) + "\n")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    hits = _STATE.get("quarantine_hits") or []
    if hits:
        terminalreporter.write_sep("-", f"{len(hits)} quarantined test(s) failed (not counted)")
        for nid in hits:
            terminalreporter.write_line(f"  {nid}")
