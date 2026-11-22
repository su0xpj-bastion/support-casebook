"""Agent, tool-use and MCP packs. AG-22..27 drive the real agent SDKs against the mock."""
from __future__ import annotations

import importlib.util

import pytest

from casebook import agent_scenarios as A
from casebook import agent_sdk_scenarios as S

HAS_CLAUDE_SDK = importlib.util.find_spec("claude_agent_sdk") is not None
HAS_OPENAI_AGENTS = importlib.util.find_spec("agents") is not None


def test_ag01_tool_result_pairing(mock) -> None:
    r = A.ag01(mock)
    assert r["no_result"]["status"] == 400
    assert r["no_result"]["message"].startswith("`tool_use` ids were found without `tool_result` blocks immediately after: toolu_01A")
    assert r["text_first"]["status"] == 400 and r["fixed"]["status"] == 200


def test_ag02_mixed_turn(mock) -> None:
    r = A.ag02(mock)
    assert r["text_added"]["status"] == 400 and "without a corresponding `web_fetch_tool_result` block" in r["text_added"]["message"]
    assert r["server_tool_dropped"]["status"] == 400 and r["server_tool_dropped"]["message"].endswith("but no `web_fetch` tool was provided")
    assert r["fixed"]["status"] == 200


def test_ag03_pause_turn_container(mock) -> None:
    r = A.ag03(mock)
    assert r["first"]["stop_reason"] == "pause_turn" and r["first"]["container"]
    assert r["without_container"].startswith("FileNotFoundError") and r["with_container"].startswith("report.csv found")


def test_ag04_server_tool_config(mock) -> None:
    r = A.ag04(mock)
    assert r["zdr_no_allowed_callers"]["status"] == 400 and r["zdr_direct"]["status"] == 200
    assert r["old_version"]["status"] == 200 and r["both_domain_lists"]["status"] == 400


def test_ag05_uncapped_loop_runs_away(mock) -> None:
    r = A.ag05(mock)
    assert r["uncapped_calls"] == 20 and r["capped_calls"] == 3


def test_ag06_strict_silently_downgraded(mock) -> None:
    r = A.ag06(mock)
    assert r["loose_echo_strict"] is False and r["fixed_echo_strict"] is True and len(r["lint"]) == 2


def test_ag07_duplicate_parallel_calls(mock) -> None:
    r = A.ag07(mock)
    assert r["parallel_calls"] == 2 and r["identical"] and r["with_parallel_false"] == 1


def test_ag08_replay_lint() -> None:
    r = A.ag08(None)
    assert r["bad"]["cls"] == "state.reasoning_dropped" and r["good"] is None


def test_ag09_chain_rebills(mock) -> None:
    r = A.ag09(mock)
    b = r["input_tokens_per_turn"]
    assert all(later > earlier for earlier, later in zip(b, b[1:])) and r["total_input_billed"] > 4 * b[0]


def test_ag10_authorization_must_be_resent(mock) -> None:
    r = A.ag10(mock)
    assert all("401" not in x for x in r["turn1"]) and "401" in r["turn2_no_token"][0]
    assert all("401" not in x for x in r["turn2_token_resent"]) and r["echoed_authorization"] is False


def test_ag11_approval_flow(mock) -> None:
    r = A.ag11(mock)
    assert "mcp_approval_request" in r["turn1"] and "mcp_call" not in r["turn1"] and "mcp_call" in r["turn2"]


def test_ag12_connector_id(mock) -> None:
    r = A.ag12(mock)
    assert r["connector_id"] == 400 and r["server_url"] == 200


def test_ag13_mcp_connector(mock) -> None:
    r = A.ag13(mock)
    assert r["undefined_server"]["status"] == 400
    assert r["unknown_tool_in_configs"]["status"] == 200
    assert [b["type"] for b in r["unknown_tool_in_configs"]["content"]] == ["text"]  # silent no-op
    assert r["correct"]["content"][0]["type"] == "mcp_tool_use"


def test_ag14_connector_needs_https(mock) -> None:
    assert A.ag14(mock)["local_server"]["status"] == 400


def test_ag15_connector_lint() -> None:
    r = A.ag15(None)
    assert len(r["bad"]) == 4 and r["good"] == []


@pytest.mark.skipif(not HAS_CLAUDE_SDK, reason="claude-agent-sdk not installed")
def test_ag22_headless_denies_but_reports_success(mock) -> None:
    r = S.ag22(mock)
    d = r["default_mode"]
    assert d["tool_ran"] is False and d["tool_result_is_error"] and "needs approval" in d["tool_result_text"]
    assert d["result"].get("subtype") == "success" and d["result"].get("is_error") is False
    assert r["with_allow_rule"]["tool_ran"] is True


@pytest.mark.skipif(not HAS_CLAUDE_SDK, reason="claude-agent-sdk not installed")
def test_ag23_can_use_tool_shadowed(mock) -> None:
    r = S.ag23(mock)
    assert r["allow_rule_plus_callback"]["callback_calls"] == [] and r["allow_rule_plus_callback"]["tool_ran"]
    assert r["allow_rule_plus_callback"]["sdk_warnings"]
    assert r["callback_only"]["callback_calls"] == ["Bash"]


@pytest.mark.skipif(not HAS_CLAUDE_SDK, reason="claude-agent-sdk not installed")
def test_ag24_allowed_tools_is_not_a_restriction(mock) -> None:
    r = S.ag24(mock)
    assert r["allowed_read_only_bypass"]["tool_ran"] is True and r["disallowed_bash"]["tool_ran"] is False


@pytest.mark.skipif(not HAS_OPENAI_AGENTS, reason="openai-agents not installed")
def test_ag26_max_turns_and_guardrail(mock) -> None:
    r = S.ag26(mock)
    assert r["max_turns"]["exception"] == "MaxTurnsExceeded" and r["model_calls"] == 3
    assert r["guardrail"]["exception"] == "InputGuardrailTripwireTriggered"


@pytest.mark.skipif(not HAS_OPENAI_AGENTS, reason="openai-agents not installed")
def test_ag27_session_and_previous_response_id_conflict(mock) -> None:
    r = S.ag27(mock)
    assert r["conflict"]["exception"] == "UserError" and "cannot be combined" in r["conflict"]["message"]


def test_ag28_replay_repeats_side_effect_unless_deduped_on_business_key(mock) -> None:
    r = S.ag28(mock)
    assert r["naive_refunds"] == 2 and len(set(r["naive_call_ids"])) == 2
    assert r["dedup_refunds"] == 1
