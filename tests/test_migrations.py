"""Migrations fail the way the docs say, and pass once fixed."""
from __future__ import annotations

from casebook import scenarios


def test_an18_each_migration_400_has_a_passing_fix(mock) -> None:
    r = scenarios.run("AN-18", mock)
    for case in ("prefill", "thinking_enabled", "sampling_params", "computer_tool_version"):
        assert r[case]["broken"]["status"] == 400, case
        assert r[case]["fixed"]["status"] == 200, case
    assert r["prefill"]["broken"]["message"].startswith("This model does not support assistant message prefill")
    assert r["thinking_enabled"]["broken"]["message"].startswith('"thinking.type.enabled" is not supported for this model')
    assert "does not support tool types: computer_20251124" in r["computer_tool_version"]["broken"]["message"]


def test_an19_same_request_200_on_sonnet_4_5_400_on_sonnet_5_5(mock) -> None:
    r = scenarios.run("AN-19", mock)
    assert r["sonnet_4_5_forced"]["status"] == 200
    assert r["sonnet_5_5_forced"]["status"] == 400
    assert r["sonnet_5_5_forced"]["message"] == 'tool_choice: type "tool" and "any" are not supported for this model.'
    assert r["sonnet_5_5_auto_strict"]["status"] == 200


def test_an20_edited_history_breaks_thinking_signature(mock) -> None:
    r = scenarios.run("AN-20", mock)
    assert r["verbatim"]["status"] == 200
    assert r["edited_history"]["status"] == 400
    assert "Invalid `signature` in `thinking` block" in r["edited_history"]["message"]


def test_an21_sdk_upgrade_two_failure_shapes(mock) -> None:
    r = scenarios.run("AN-21", mock)
    assert r["typed_param"]["status"] is None and "temperature" in r["typed_param"]["message"]
    assert r["versions_sent"][0] == "2026-10-21"
    assert r["sdk_default_version"]["status"] == 400
    assert r["pinned_2023_06_01"]["status"] == 200
    assert r["default_without_temperature"]["status"] == 200


def test_an26_retirement_flips_on_the_date(mock) -> None:
    r = scenarios.run("AN-26", mock)
    assert r["2026-11-29"]["status"] == 200 and r["2026-11-30"]["status"] == 404
    assert r["replacement"]["status"] == 200


def test_oa24_shutdown_flips_on_the_date(mock) -> None:
    r = scenarios.run("OA-24", mock)
    assert r["gpt-4o 2027-01-13"]["status"] == 200
    assert r["gpt-4o 2027-01-14"]["status"] == 404 and r["gpt-4o 2027-01-14"]["code"] == "model_not_found"
    assert r["gpt-4o-mini 2027-01-14"]["status"] == 404


def test_retired_status_codes_are_tagged_synthesized() -> None:
    from casebook import packs

    for pid in ("AN-26", "OA-24"):
        assert packs.get(pid).expected["provenance"]["status"] == "synthesized"


def test_an26_sdk_emits_deprecation_warning_with_the_date(mock) -> None:
    """anthropic 2.9.0 warns on deprecated model ids — but DeprecationWarning is hidden by default
    outside __main__, so a production service usually never shows it."""
    import warnings

    import pytest

    from casebook.repro import make_client, request_kwargs

    c = make_client("anthropic", mock.url, max_retries=0)
    with pytest.warns(DeprecationWarning, match="November 30th, 2026"):
        c.messages.create(**request_kwargs("anthropic", {"model": "claude-sonnet-4-5-20250929"}),
                          extra_headers={"X-Casebook-Date": "2026-11-19"})
    # Outside pytest, the interpreter's default filters show DeprecationWarning only for code in __main__.
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-c", "import warnings;print([(f[0], f[3]) for f in warnings.filters "
                          "if f[2] is DeprecationWarning])"], capture_output=True, text=True, check=True).stdout
    assert "('ignore', None)" in out and "('default', '__main__')" in out
    _ = warnings
