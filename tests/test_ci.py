"""CI tooling: selection, quarantine, plugin, sharding, signatures, merge queue, runner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from casebook.ci import mergequeue as MQ
from casebook.ci import quarantine as Q
from casebook.ci import runner
from casebook.ci import select as S
from casebook.ci import shard as SH
from casebook.ci import signatures as SG

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "ci_cases"


def _pytest(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=120)


# ---------------------------------------------------------------- selection

@pytest.fixture(scope="module")
def graph() -> dict[str, set[str]]:
    return S.import_graph()


def test_sel_01_leaf_module_selects_only_its_tests(graph) -> None:
    sel = S.select(["casebook/exposure.py"], graph=graph)
    assert not sel.run_all and sel.files == {"tests/test_exposure.py", "tests/test_ci_graph.py"}   # the graph check imports every test module


def test_sel_02_shared_module_selects_every_importer(graph) -> None:
    sel = S.select(["casebook/redact.py"], graph=graph)
    assert {"tests/test_redact.py", "tests/test_packet.py", "tests/test_triage.py"} <= sel.files
    assert not sel.run_all


def test_sel_03_docs_only_change_selects_nothing(graph) -> None:
    sel = S.select(["docs/workflows.md", "README.md", "runbooks/AN-01.md"], graph=graph)
    assert sel.pytest_args() == []


def test_sel_04_unknown_or_global_files_run_everything(graph) -> None:
    for path in ("Makefile", "requirements.txt", "tests/conftest.py", "casebook/ci/pytest_plugin.py"):
        assert S.select([path], graph=graph).run_all, path


def test_sel_05_pack_change_selects_that_pack_and_all_pack_readers(graph) -> None:
    collected = [
        "tests/test_packs.py::test_packet_valid[AN-01]", "tests/test_packs.py::test_packet_valid[AN-03]",
        "tests/test_packs.py::test_an01_has_no_retry_after_and_an03_does", "tests/test_identity.py::test_id01_nameid",
    ]
    sel = S.select(["faults/anthropic/AN-01-enforced-spend-limit/expected.json"], collected=collected, graph=graph)
    assert sel.nodes == {collected[0], collected[2]}
    assert "tests/test_triage.py" in sel.files


def test_sel_06_runtime_data_counts_as_a_module_change(graph) -> None:
    assert "tests/test_ts_sdks.py" in S.select(["ts/repro.mjs"], graph=graph).files
    assert "tests/test_packet.py" in S.select(["casebook/schema/packet.schema.json"], graph=graph).files


def test_sel_07_deleted_module_runs_everything(graph) -> None:
    assert S.select(["casebook/no_longer_here.py"], graph=graph).run_all


# ---------------------------------------------------------------- quarantine

def _row(nid: str, sha: str, outcome: str, ts: float = 0.0, run: str = "r") -> dict:
    return {"run": run, "sha": sha, "attempt": 1, "nodeid": nid, "outcome": outcome, "duration": 0.1,
            "message": "boom" if outcome == "failed" else "", "ts": ts}


def test_qua_01_pass_and_fail_on_one_commit_is_flaky_fail_every_time_is_not() -> None:
    rows = [_row("t::flaky", "a", "failed"), _row("t::flaky", "a", "passed"),
            _row("t::broken", "a", "failed"), _row("t::broken", "a", "failed")]
    s = Q.analyse(rows)
    assert s["t::flaky"]["flaky_commits"] == ["a"]
    assert s["t::broken"]["flaky_commits"] == [] and s["t::broken"]["consistent_fail_commits"] == ["a"]
    new, added, _ = Q.update(rows, {"tests": []})
    assert added == ["t::flaky"] and [t["nodeid"] for t in new["tests"]] == ["t::flaky"]


def test_qua_02_released_after_enough_clean_runs() -> None:
    policy = Q.QuarantinePolicy(release_after=3)
    cur = {"tests": [{"nodeid": "t::x", "since": 10.0}]}
    rows = [_row("t::x", "b", "passed", ts=11 + i) for i in range(2)]
    assert Q.update(rows, cur, policy)[2] == []
    rows.append(_row("t::x", "b", "passed", ts=20))
    assert Q.update(rows, cur, policy)[2] == ["t::x"]


def test_qua_03_plugin_records_history_and_quarantine_keeps_build_green(tmp_path: Path) -> None:
    case = "tests/ci_cases/case_assertion.py"
    nid = f"{case}::test_spend_cap_is_retryable"
    hist = tmp_path / "h.jsonl"
    plain = _pytest(case, f"--ci-history={hist}", f"--ci-quarantine-file={tmp_path / 'none.json'}")
    assert plain.returncode == 1
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"tests": [{"nodeid": nid}]}))
    quarantined = _pytest(case, f"--ci-history={hist}", f"--ci-quarantine-file={qfile}")
    assert quarantined.returncode == 0, quarantined.stdout
    assert "1 quarantined test(s) failed" in quarantined.stdout
    rows = Q.load_history(hist)
    assert [r["outcome"] for r in rows] == ["failed", "failed"]      # history keeps the truth
    assert all(r["nodeid"] == nid and r["duration"] > 0 for r in rows)


# ---------------------------------------------------------------- sharding

def test_shd_01_every_file_once_and_balanced() -> None:
    durations = {"a": 100.0, "b": 60.0, "c": 50.0, "d": 30.0, "e": 20.0, "f": 10.0}
    plan = SH.plan(sorted(durations), 3, durations)
    files = [f for s in plan for f in s["files"]]
    assert sorted(files) == sorted(durations)
    assert max(s["seconds"] for s in plan) == 100.0          # the longest file sets the floor
    assert sorted(s["seconds"] for s in plan) == [80.0, 90.0, 100.0]


def test_shd_02_file_durations_sum_tests_per_run() -> None:
    rows = [_row("tests/x.py::a", "s", "passed", run="1"), _row("tests/x.py::b", "s", "passed", run="1"),
            _row("tests/x.py::a", "s", "passed", run="2")]
    assert SH.file_durations(rows) == {"tests/x.py": 0.15}


# ---------------------------------------------------------------- signatures

EXPECTED = {
    "case_port_in_use.py": "CI-01", "case_tempdir_race.py": "CI-02", "case_subprocess_timeout.py": "CI-03",
    "case_dns.py": "CI-04", "case_node_module.py": "CI-05", "case_python_module.py": "CI-06",
    "case_syntax.py": "CI-07", "case_assertion.py": "CI-10",
}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_sig_01_signature_matches_reproduced_failure(case: str) -> None:
    out = _pytest(str(CASES / case))
    assert out.returncode != 0, f"{case} was supposed to fail"
    assert SG.classify(out.stdout + out.stderr).id == EXPECTED[case]


def test_sig_02_stale_generated_file(tmp_path: Path) -> None:
    def git(*a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True)
    git("init", "-q")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test-matrix.md").write_text("old\n")
    git("add", "."), git("-c", "user.email=ci@example.invalid", "-c", "user.name=ci", "commit", "-qm", "x")
    (tmp_path / "docs" / "test-matrix.md").write_text("new\n")
    out = git("diff", "--exit-code", "docs/test-matrix.md")
    assert out.returncode == 1 and SG.classify(out.stdout).id == "CI-08"


def test_sig_03_broken_doc_link(tmp_path: Path) -> None:
    doc = tmp_path / "broken.md"
    doc.write_text("[gone](does-not-exist.md)\n")
    code = (f"import sys; sys.path.insert(0, 'tools'); import check_docs as c; "
            f"from pathlib import Path; c.DOCS = [Path({str(doc)!r})]; raise SystemExit(c.main())")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1 and SG.classify(out.stdout).id == "CI-09"


def test_sig_04_unknown_text_is_conditional() -> None:
    s = SG.classify("something nobody has seen before")
    assert s.id == "CI-00" and s.retry == "C"


def test_sig_05_every_signature_is_reproduced() -> None:
    covered = set(EXPECTED.values()) | {"CI-08", "CI-09"}
    assert covered == {s.id for s in SG.SIGNATURES}


# ---------------------------------------------------------------- merge queue

def _repo(tmp: Path) -> Path:
    r = tmp / "repo"
    r.mkdir()
    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=r, check=True, capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "ci@example.invalid"), git("config", "user.name", "ci")
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (r / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    git("add", "."), git("commit", "-qm", "base")
    for name, path, text in [
        ("feat-1", "f1.py", "X = 1\n"),
        ("feat-2", "f2.py", "X = 2\n"),
        ("breaks", "calc.py", "def add(a, b):\n    return a - b\n"),          # tests fail
        ("feat-3", "f3.py", "X = 3\n"),
        ("conflict-a", "shared.py", "A = 1\n"),
        ("conflict-b", "shared.py", "B = 2\n"),                              # conflicts with conflict-a
    ]:
        git("checkout", "-q", "-b", name, "main")
        (r / path).write_text(text)
        git("add", "."), git("commit", "-qm", name)
        git("checkout", "-q", "main")
    return r


QUEUE = ["feat-1", "feat-2", "breaks", "feat-3", "conflict-a", "conflict-b"]


@pytest.mark.parametrize("batch", [1, 4])
def test_mq_01_lands_good_branches_ejects_bad_ones(tmp_path: Path, batch: int) -> None:
    repo = _repo(tmp_path)
    res = MQ.MergeQueue(repo, "main", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                        batch_size=batch).process(QUEUE)
    assert sorted(res.landed) == ["conflict-a", "feat-1", "feat-2", "feat-3"]
    assert res.ejected == {"breaks": "tests failed", "conflict-b": "conflict"}
    final = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                           capture_output=True, text=True)
    assert final.returncode == 0                                         # main never breaks
    # serial: one run per non-conflicting branch. Batch of 4 with a bad branch inside: 1 failed batch run
    # + bisection (2 halves, then 2 singles) + 1 run for the last batch = 6, MORE than serial.
    assert res.test_runs == (5 if batch == 1 else 6)


@pytest.mark.parametrize("batch,runs", [(1, 4), (4, 1)])
def test_mq_03_batching_wins_when_nothing_fails(tmp_path: Path, batch: int, runs: int) -> None:
    repo = _repo(tmp_path)
    res = MQ.MergeQueue(repo, "main", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                        batch_size=batch).process(["feat-1", "feat-2", "feat-3", "conflict-a"])
    assert len(res.landed) == 4 and res.test_runs == runs


def test_mq_02_base_that_moved_is_not_overwritten(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    q = MQ.MergeQueue(repo, "main", [sys.executable, "-c", "pass"])
    stale = MQ._rev(repo, "main")
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "someone else"], cwd=repo, check=True)
    with pytest.raises(subprocess.CalledProcessError):
        q._advance(stale, MQ._rev(repo, "feat-1"))


# ---------------------------------------------------------------- runner

def test_run_01_flaky_failure_passes_on_rerun_and_is_reported(tmp_path: Path, monkeypatch) -> None:
    case = "tests/ci_cases/case_flaky.py"
    monkeypatch.setenv("CASEBOOK_FLAKY_COUNTER", str(tmp_path / "n"))
    monkeypatch.setattr(runner, "targets", lambda *a: ([case], {}))
    monkeypatch.setattr(Q, "active_nodeids", lambda *a: set())
    report = tmp_path / "r.json"
    code = runner.run(history=tmp_path / "h.jsonl", report=report)
    data = json.loads(report.read_text())
    assert code == 0
    assert [(f["result"], f["attempts"]) for f in data["failures"]] == [("flaky", ["failed", "passed"])]
    rows = Q.load_history(tmp_path / "h.jsonl")
    assert Q.analyse(rows)[f"{case}::test_fails_on_first_attempt_only"]["flaky_commits"]


def test_run_02_consistent_failure_fails_the_build_with_its_signature(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "targets", lambda *a: (["tests/ci_cases/case_assertion.py"], {}))
    monkeypatch.setattr(Q, "active_nodeids", lambda *a: set())
    report = tmp_path / "r.json"
    assert runner.run(history=tmp_path / "h.jsonl", report=report, reruns=2) == 1
    f = json.loads(report.read_text())["failures"][0]
    assert f["result"] == "failed" and f["attempts"] == ["failed", "failed", "failed"] and f["signature"] == "CI-10"
