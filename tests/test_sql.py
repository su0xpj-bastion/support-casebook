"""SQL over the casebook's evidence: the saved queries must reproduce the published numbers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from casebook import logdb

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("sql") / "casebook.db"
    logdb.load(path, repro_packs=["AN-01", "AN-03", "AN-11", "AN-16", "OA-04", "OA-14"])
    return path


def q(db: Path, name: str, **params: str) -> list[dict]:
    return [dict(r) for r in logdb.run_query(name, params, db)]


def test_sql_01_every_saved_query_runs_and_says_what_it_answers(db) -> None:
    for name in logdb.saved_queries():
        params = {"pack": "AN-11"} if name == "request_trace" else {}
        q(db, name, **params)
        assert logdb.describe(name), name


def test_sql_02_triage_numbers_match_the_published_eval(db) -> None:
    rows = {(r["model"], r["mode"]): r for r in q(db, "triage_false_retry")}
    for f in sorted((ROOT / "triage" / "eval").glob("results-*.json")):
        res = json.loads(f.read_text())
        model = f.stem.removeprefix("results-")
        for mode in ("rules", "llm", "combined"):
            m = res[mode]["all"]
            r = rows[(model, mode)]
            assert (r["false_retry"], r["wrong"], r["llm_errors"], r["leaks"]) == (
                m["false_retryable"], m["wrong_class_not_abstain"], m["llm_errors"], m["redaction_leaks"]), (model, mode)


def test_sql_03_same_status_needs_different_fixes(db) -> None:
    rows = {r["status"]: r for r in q(db, "same_status_different_fix")}
    assert rows[429]["distinct_verdicts"] >= 5
    assert "AN-01=N" in rows[429]["cases_and_retry"] and "AN-03=Y" in rows[429]["cases_and_retry"]


def test_sql_04_wasted_retries_are_the_non_retryable_billing_errors(db) -> None:
    rows = {r["pack"]: r for r in q(db, "wasted_retries")}
    assert set(rows) == {"AN-01", "OA-04"}                      # of the packs loaded; AN-03 is retryable
    assert all(r["sdk_default_requests"] == 3 and r["policy_requests"] == 1 for r in rows.values())


def test_sql_05_request_trace_shows_three_ids_for_one_timed_out_call(db) -> None:
    rows = [r for r in q(db, "request_trace", pack="AN-11") if r["mode"] == "sdk-default"]
    assert [r["retry_count"] for r in rows] == [0, 1, 2]
    assert len({r["request_id"] for r in rows}) == 3


def test_sql_06_failures_without_a_request_id(db) -> None:
    assert {r["pack"] for r in q(db, "missing_request_id")} == {"AN-16", "OA-14"}


def test_sql_07_live_captures_loaded(db) -> None:
    rows = {r["pack"]: r for r in q(db, "live_vs_expected")}
    assert set(rows) == {"AN-13", "AN-16", "OA-07"}
    assert rows["AN-13"]["matched"] == "4/4" and rows["AN-13"]["x_should_retry"] == "false"
