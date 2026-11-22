"""Triage: rules gates on every ticket, the LLM guard with a fake model, split hygiene, and the
committed eval report's gates. No network, no model needed."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from casebook.triage import classify as C
from casebook.triage import dataset as D

TICKETS = D.synthetic() + D.adversarial()


class FakeLLM:
    """Stands in for a model: answers with a fixed JSON and records every prompt it was sent."""

    def __init__(self, answer: dict) -> None:
        self.answer, self.prompts = answer, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.prompts.append(kw["messages"])
        msg = SimpleNamespace(content=json.dumps(self.answer))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_dataset_shape() -> None:
    assert len(TICKETS) == 260 and sum(t.source == "adversarial" for t in TICKETS) == 20
    assert sum(t.planted_secret for t in TICKETS) > 40
    assert set(D.LABELS) >= {t.gold for t in TICKETS}


def test_split_is_by_family() -> None:
    train, test = D.split(TICKETS)
    assert {t.family for t in train}.isdisjoint(D.TEST_FAMILIES)
    assert {t.family for t in test} <= D.TEST_FAMILIES


def test_rules_gates_on_every_ticket() -> None:
    rs = [C.triage_rules(t) for t in TICKETS]
    by = {t.id: t for t in TICKETS}
    assert sum(D.LABELS[r.label] == "Y" and by[r.ticket].gold_retry == "N" for r in rs) == 0
    assert all(r.label == by[r.ticket].gold or (by[r.ticket].abstain_ok and r.label == "needs_more_info") for r in rs)


def test_bare_429_asks_for_more_info() -> None:
    adv = {t.id: t for t in D.adversarial()}
    assert C.triage_rules(adv["ADV-08"]).label == "needs_more_info"
    assert C.triage_rules(adv["ADV-09"]).label == "needs_more_info"


def test_specific_message_beats_generic_type() -> None:
    adv = {t.id: t for t in D.adversarial()}
    assert C.triage_rules(adv["ADV-06"]).label == "auth.cors_blocked"      # 401 authentication_error + CORS message
    assert C.triage_rules(adv["ADV-01"]).label == "billing.tier_spend_cap"  # customer says 'rate limited'


def test_guard_blocks_retryable_guess_without_code() -> None:
    t = next(x for x in D.adversarial() if x.id == "ADV-07")               # vague, gold needs_more_info
    llm = FakeLLM({"class": "limits.rate", "evidence": ["sometimes fails"]})
    assert C.triage_llm(t, llm, "fake", []).label == "limits.rate"           # unguarded model output
    r = C.triage_combined(t, llm, "fake", [])
    assert r.label == "needs_more_info" and r.basis.startswith("guard")


def test_guard_rejects_invented_quotes() -> None:
    t = next(x for x in D.adversarial() if x.id == "ADV-07")
    llm = FakeLLM({"class": "auth.invalid", "evidence": ["401 Incorrect API key provided"]})
    r = C.triage_combined(t, llm, "fake", [])
    assert r.label == "needs_more_info" and r.unsupported


def test_secrets_never_reach_the_model() -> None:
    t = next(x for x in TICKETS if x.planted_secret)
    llm = FakeLLM({"class": "needs_more_info", "evidence": []})
    C.triage_llm(t, llm, "fake", [x for x in TICKETS if x.planted_secret][:3])
    assert all(D.PLANTED not in m["content"] for m in llm.prompts[0])


REPORTS = sorted((Path(__file__).resolve().parents[1] / "triage" / "eval").glob("results-*.json"))


@pytest.mark.parametrize("path", REPORTS, ids=lambda p: p.stem)
def test_committed_report_gates(path: Path) -> None:
    r = json.loads(path.read_text())
    assert r["rules"]["all"]["false_retryable"] == 0 and r["combined"]["all"]["false_retryable"] == 0
    assert r["combined"]["all"]["redaction_leaks"] == 0


def test_llm_errors_are_labelled_not_hidden() -> None:
    """A model that returns garbage must show up as an error, not as a cautious abstain."""
    class Broken(FakeLLM):
        def _create(self, **kw):
            msg = SimpleNamespace(content="not json at all")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    t = next(x for x in D.adversarial() if x.id == "ADV-07")
    for fn in (C.triage_llm, C.triage_combined):
        r = fn(t, Broken({}), "fake", [])
        assert r.label == "needs_more_info" and r.basis.startswith("llm error")
