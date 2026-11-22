"""Exposure scanner: dated, located, no false positives on a clean integration."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from casebook import exposure

D = Path(__file__).resolve().parent.parent / "cases" / "dossiers"


def _scan(name: str, on: str = "2026-11-19") -> dict:
    return exposure.scan(D / f"{name}.yaml", date.fromisoformat(on))


def _rules(res: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in res["findings"]:
        out.setdefault(f["rule"], []).append(f)
    return out


def test_exp_01_sonnet_4_5_flagged_with_date_and_replacement_in_code_and_yaml() -> None:
    r = _rules(_scan("acme-insurer"))
    ret = r["model.retirement"]
    assert {f["where"].split("/")[-1] for f in ret} == {"triage.py:4", "subagents.yaml:3"}
    assert all(f["deadline"] == "2026-11-30" and f["severity"] == "P1" and "claude-sonnet-5-5" in f["fix"] for f in ret)


def test_exp_02_forced_tool_choice_thinking_and_sampling_flagged() -> None:
    r = _rules(_scan("acme-insurer"))
    assert r["tools.forced_tool_choice"][0]["pack"] == "AN-19"
    assert r["thinking.enabled"][0]["pack"] == "AN-18"
    assert r["params.sampling"][0]["where"].endswith("triage.py:11")
    assert r["sdk.major_upgrade"][0]["deadline"] == "2026-12-08"


def test_exp_03_openai_gpt4o_and_assistants() -> None:
    r = _rules(_scan("northwind-labs"))
    assert r["model.retirement"][0]["deadline"] == "2027-01-14" and r["model.retirement"][0]["severity"] == "P2"
    assert r["api.assistants"][0]["severity"] == "P1"
    assert r["stream.unguarded_choices"][0]["where"].endswith("chat.py:10")
    assert r["retry.sdk_default_on_billing_429"][0]["pack"] == "OA-04"


def test_exp_04_clean_integration_has_no_findings() -> None:
    assert _scan("clean-co")["findings"] == []


def test_exp_05_severity_escalates_as_the_date_approaches() -> None:
    far = _rules(_scan("northwind-labs", "2026-09-01"))["model.retirement"][0]
    near = _rules(_scan("northwind-labs", "2027-01-05"))["model.retirement"][0]
    after = _rules(_scan("northwind-labs", "2027-01-20"))["model.retirement"][0]
    assert (far["severity"], near["severity"], after["severity"]) == ("P3", "P1", "P1")
    assert "retired" in after["evidence"]


def test_exp_06_draft_is_a_draft_with_customer_paragraph() -> None:
    res = _scan("acme-insurer")
    note = exposure.draft_note(res, yaml.safe_load((D / "acme-insurer.yaml").read_text()))
    assert "not sent" in note and "## Customer-ready paragraph" in note
    assert "retires on 2026-11-30" in note and "claude-sonnet-5-5" in note
    assert "(fictional)" in note
