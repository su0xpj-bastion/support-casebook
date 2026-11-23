from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dre.grader import grade, match, split_items
from dre.judge import kappa
from dre.run import checks, drop_p_value, evaluate, load, summarize, wilson
from dre.systems import MockSystem

ROOT = Path(__file__).resolve().parent.parent


# ---------------- data ----------------

def test_slice_is_fixed_stratified_and_attributed():
    rows = load()
    assert len(rows) == 60 and len({r["id"] for r in rows}) == 60
    assert sum(r["answer_type"] == "Single Answer" for r in rows) == 21
    assert len({r["category"] for r in rows}) >= 15
    src = (ROOT / "data" / "SOURCE.md").read_text()
    assert "Apache License 2.0" in src and "b2623f8653065c2672de6d941fc5434cd652376c" in src


def test_every_gold_answer_grades_correct_against_itself():
    for r in load():
        assert grade(f"FINAL ANSWER: {r['answer']}", r["answer"], r["answer_type"]).correct, r["id"]


# ---------------- grader ----------------

@pytest.mark.parametrize("pred,gold,ok", [
    ("New Zealand", "New Zealand", True), ("new zealand.", "New Zealand", True),
    ("Hasbro", "Hasbro, Inc.", True), ("Autonomous Univ. of San Luis Potosi", "Autonomous University of San Luis Potosí", True),
    ("2019", "2018", False), ("Austria", "Australia", False),
    ("The answer is clearly New Zealand, although Australia and Canada were close in 2021", "New Zealand", False),
])
def test_match(pred, gold, ok):
    assert match(pred, gold) is ok


def test_split_keeps_parentheses_and_drops_headings_and_conjunctions():
    assert split_items("Rebecca West (1969, 1970), Antonia Fraser (1970, 1971)") == [
        "Rebecca West (1969, 1970)", "Antonia Fraser (1970, 1971)"]
    assert split_items("Recipients:\nA, B, and C") == ["A", "B", "C"]
    assert split_items("Asthma, \nBronchitis,\nEmphysema, or\nPneumonia") == ["Asthma", "Bronchitis", "Emphysema", "Pneumonia"]


def test_list_grading_kinds():
    gold = "Austria, Switzerland, Singapore"
    assert grade("FINAL ANSWER: Austria; Switzerland; Singapore", gold, "Set Answer").kind == "correct"
    g = grade("FINAL ANSWER: Austria; Singapore", gold, "Set Answer")
    assert (g.kind, round(g.recall, 2), g.precision) == ("incomplete", 0.67, 1.0)
    g = grade("FINAL ANSWER: Austria; Switzerland; Singapore; Japan", gold, "Set Answer")
    assert (g.kind, g.recall, g.precision) == ("extra_items", 1.0, 0.75)
    assert grade("I could not find it.", gold, "Set Answer").kind == "wrong"
    assert grade("", gold, "Set Answer").kind == "no_answer"


def test_missing_final_line_falls_back_to_last_line_but_is_flagged():
    g = grade("Research notes...\nNew Zealand", "New Zealand", "Single Answer")
    assert g.correct and not g.has_final


# ---------------- statistics ----------------

def test_drop_p_value_and_wilson():
    assert drop_p_value(40, 60, 40, 60) == pytest.approx(0.5)
    assert drop_p_value(40, 60, 23, 60) < 0.01
    assert drop_p_value(40, 60, 37, 60) > 0.10
    lo, hi = wilson(40, 60)
    assert 0.53 < lo < 0.55 and 0.77 < hi < 0.79


def test_kappa():
    assert kappa([(True, True), (False, False)] * 5) == 1.0
    assert kappa([(True, False), (False, True)] * 5) < 0


# ---------------- faults ----------------

BASE = summarize(evaluate(MockSystem(), load()), "mock")
FAULT_CATCH = {
    "drops_items": {"accuracy_vs_baseline", "list_recall_vs_baseline"},
    "hallucinated_extras": {"list_precision", "accuracy_vs_baseline"},
    "no_citations": {"citations_present"},
    "format_drift": {"format_compliance"},
    "wrong_singles": {"accuracy_vs_baseline"},
}


def failed(faults: set[str], repeats: int = 1) -> set[str]:
    s = summarize(evaluate(MockSystem(faults), load(), repeats), "mock")
    return {c["check"] for c in checks(s, BASE) if c["status"] == "fail"}


def test_healthy_system_passes_every_check():
    assert failed(set(), repeats=3) == set()


@pytest.mark.parametrize("fault", sorted(FAULT_CATCH))
def test_fault_caught_by_exactly_its_checks(fault):
    assert failed({fault}) == FAULT_CATCH[fault]


def test_flaky_system_fails_consistency_only_with_repeats():
    assert failed({"flaky"}, repeats=1) <= {"accuracy_vs_baseline"}      # one run can't see flakiness
    assert "consistency" in failed({"flaky"}, repeats=3)


def test_format_drift_keeps_accuracy_so_only_the_format_gate_sees_it():
    s = summarize(evaluate(MockSystem({"format_drift"}), load()), "mock")
    assert s["correct"] == BASE["correct"] and s["format_rate"] == 0


def test_request_errors_are_scored_not_raised():
    class Broken:
        def ask(self, item, attempt=0):
            raise TimeoutError("upstream timeout")
    rows = evaluate(Broken(), load(3))
    assert all(r["answer"]["error"].startswith("TimeoutError") and not r["grade"]["correct"] for r in rows)


# ---------------- CLI ----------------

def test_cli_mock_run_and_judge(tmp_path):
    out = tmp_path / "run"
    p = subprocess.run([sys.executable, "-m", "dre", "run", "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    data = json.loads((out / "results.json").read_text())
    assert data["meta"]["verdict"] == "PASS" and data["summary"]["n"] == 60
    j = subprocess.run([sys.executable, "-m", "dre.judge", str(out / "results.json")], cwd=ROOT, capture_output=True, text=True)
    assert j.returncode == 0 and "Cohen's kappa" in j.stdout


def test_cli_fault_run_fails(tmp_path):
    p = subprocess.run([sys.executable, "-m", "dre", "run", "--faults", "no_citations", "--out", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 1
