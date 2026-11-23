"""Every scenario has a fault it must catch, on every protocol it can see it on.

For each fault the full scenario set runs through all three adapters against the mock,
and the exact set of failing (protocol, scenario) pairs is pinned.
"""
from __future__ import annotations

import pytest

from ace.adapters import ADAPTERS
from ace.scenarios import SCENARIOS, run
from mockserve.server import serve

ALL = ("chat", "responses", "messages")


def on(protocols, *scenarios):
    return {(p, s) for p in protocols for s in scenarios}


FAULT_CATCH = {
    # Anthropic reports no total_tokens, so there is no sum to contradict: not visible there
    "usage_drift": on(("chat", "responses"), "usage_math"),
    "stream_no_usage": on(ALL, "stream_basic", "usage_math", "prompt_cache"),
    "ignore_max_tokens": on(ALL, "max_output"),
    "stop_reason_wrong": on(ALL, "max_output"),
    "bad_tool_args": on(ALL, "tool_roundtrip", "parallel_tools"),
    "schema_violation": on(ALL, "structured_output"),
    "tool_result_ignored": on(ALL, "tool_roundtrip", "tool_error_recovery"),
    "injection_follows": on(ALL, "injection_resistance"),
    "history_dropped": on(ALL, "multi_turn_recall"),
    "cache_never_hits": on(ALL, "prompt_cache"),
    "handoff_misroute": on(ALL, "handoff_routing"),
    "error_shape": on(ALL, "error_contract"),
}


def failing(faults: set[str]) -> set[tuple[str, str]]:
    server = serve(0, faults)
    try:
        out = set()
        for proto in ALL:
            base = f"http://127.0.0.1:{server.server_port}" + ("" if proto == "messages" else "/v1")
            adapter = ADAPTERS[proto](f"mock-{proto}", "mock-model", base, "test-key", {})
            out |= {(proto, o.scenario) for o in run(adapter) if o.status == "fail"}
        return out
    finally:
        server.shutdown()


def test_healthy_mock_passes_everything_on_every_protocol():
    assert failing(set()) == set()


@pytest.mark.parametrize("fault", sorted(FAULT_CATCH))
def test_fault_caught_by_exactly_its_scenarios(fault):
    assert failing({fault}) == FAULT_CATCH[fault]


def test_every_scenario_is_guarded_by_a_fault():
    guarded = {s for pairs in FAULT_CATCH.values() for _, s in pairs}
    assert guarded == {fn.__name__ for fn in SCENARIOS}
