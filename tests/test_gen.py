"""Generators and the update linter."""
from __future__ import annotations

import datetime as dt

from casebook.gen.updates import RCA_SECTIONS, UPDATE_SECTIONS, customer_update, escalation_brief, lint_update, rca

NOW = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)


def _u(**kw) -> str:
    base = dict(title="Claims API failing", severity="P1", facts=["429 enforced_spend_limit_reached (request id req_011X)"],
                impact="All claims calls fail.", mitigation="Tier increase requested.", owner="Support",
                next_update=NOW + dt.timedelta(minutes=30), unknowns=["approval time"], retryable="N", now=NOW)
    base.update(kw)
    return customer_update(**base)


def test_gen_01_all_sections_and_clean() -> None:
    u = _u()
    assert all(f"**{s}**" in u for s in UPDATE_SECTIONS)
    assert lint_update(u, retryable="N", severity="P1") == []
    assert "Retrying will not help" in u


def test_gen_02_escalation_has_ids_window_blast_radius() -> None:
    b = escalation_brief(title="x", packet={"request_ids": {"provider": "req_1", "client": "cli-1", "cloud": None}, "org_id": "org-1",
                         "project_or_workspace": "proj", "http": {"status": 503}, "error": {"type": "service_unavailable_error",
                         "code": "server_is_overloaded"}, "sdk": {"name": "openai-python"}},
                         verdict={"cls": "provider.capacity", "retryable": "Y", "owner": "provider", "basis": "code"},
                         repro="python -m casebook repro OA-10", window=(NOW, NOW + dt.timedelta(minutes=20)),
                         blast_radius="3 keys", ask="ETA?")
    for needle in ("req_1", "cli-1", "2026-11-21 09:00 UTC", "3 keys", "repro OA-10"):
        assert needle in b


def test_gen_03_rca_sections() -> None:
    r = rca(title="t", summary="s", impact="i", root_cause="r", evidence=["e"], mitigation=["m"], prevention=["p"])
    assert all(f"## {s}" in r for s in RCA_SECTIONS) and "Evidence:" in r


def test_lint_01_root_cause_without_evidence() -> None:
    u = _u(facts=["calls fail"], impact="Root cause is a bad deploy on our side.")
    assert any("root cause" in p for p in lint_update(u))


def test_lint_02_secret() -> None:
    assert any("secrets" in p for p in lint_update(_u(facts=["key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA leaked"])))


def test_lint_03_retry_advice_on_non_retryable() -> None:
    u = _u(mitigation="Please retry the request in a minute.", retryable=None)
    assert any("non-retryable" in p for p in lint_update(u, retryable="N"))


def test_lint_04_p1_needs_next_update_time() -> None:
    u = _u(next_update=None)
    assert any("next-update" in p for p in lint_update(u, severity="P1"))
    assert lint_update(_u(next_update=None, resolved=True), retryable="N", severity="P1") == []


def test_lint_05_missing_section() -> None:
    assert lint_update(_u().replace("**Owner**", "")) == ["missing section: Owner"]
