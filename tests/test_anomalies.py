"""HTTP-200 failures and SDK-side refusals: nothing errors, the outcome is still wrong."""
from __future__ import annotations

from casebook import checks, scenarios


def test_oa13_tier_downgrade_detected(mock) -> None:
    r = scenarios.run("OA-13", mock)
    assert r["finding"]["cls"] == "billing.tier_downgraded"
    assert r["served"] == "default" and r["requests_sent"] == 1


def test_oa15_wrong_org_detected(mock) -> None:
    r = scenarios.run("OA-15", mock)
    assert r["finding"]["cls"] == "billing.wrong_org"
    assert r["packet"]["org_id"] == "org-personal-sandbox"


def test_an06_cache_below_minimum_no_error(mock) -> None:
    r = scenarios.run("AN-06", mock)
    assert r["finding"]["cls"] == "cache.below_minimum"
    for u in r["usages"]:
        assert u["cache_creation_input_tokens"] == 0 and u["cache_read_input_tokens"] == 0


def test_an07_tool_reorder_breaks_cache_then_stable_order_reads(mock) -> None:
    r = scenarios.run("AN-07", mock)
    first, drifted, fixed = r["usages"]
    assert first["cache_creation_input_tokens"] > 0
    assert drifted["cache_creation_input_tokens"] > 0 and drifted["cache_read_input_tokens"] == 0
    assert fixed["cache_read_input_tokens"] > 0
    assert r["finding"]["cls"] == "cache.prefix_drift"
    assert r["where"] == "tools (same tools, different order)"


def test_an10_sdk_refuses_before_sending(mock) -> None:
    r = scenarios.run("AN-10", mock)
    assert r["exception"] == "ValueError" and r["requests_sent"] == 0
    assert r["message"].startswith("Streaming is required for operations that may take longer than 10 minutes")


def test_checks_quiet_when_nothing_wrong() -> None:
    assert checks.service_tier("auto", "default") is None
    assert checks.service_tier("priority", "priority") is None
    assert checks.org_mismatch("org-a", "org-a") is None
    assert checks.cache({"cache_creation_input_tokens": 0, "cache_read_input_tokens": 900},
                        cache_control_sent=True, repeat=True) is None
    assert checks.first_prefix_change({"tools": [1], "system": "s"}, {"tools": [1], "system": "s"}) is None
    assert checks.first_prefix_change({"tools": [1], "system": "s"}, {"tools": [1], "system": "t"}) == "system"


# ---------------------------------------------------------------- streams, structured output, batches

def test_oa16_sdk_default_timeouts_are_ten_minutes(mock) -> None:
    r = scenarios.run("OA-16", mock)
    assert r["openai_timeout_s"] == 600 and r["anthropic_timeout_s"] == 600


def test_oa17_naive_index_crashes_guarded_loop_gets_usage_and_cut_loses_it(mock) -> None:
    r = scenarios.run("OA-17", mock)
    assert r["naive_error"] and r["naive_error"].startswith("IndexError")
    assert r["text"] == "Claim #4411: water damage."
    assert r["usage"] and r["usage"]["completion_tokens"] == 6
    assert r["cut_usage"] is None and r["finding"]["cls"] == "stream.usage_lost"


def test_oa18_failed_event_after_200_raises_nothing(mock) -> None:
    r = scenarios.run("OA-18", mock)
    assert r["last_event"] == "response.failed" and r["partial_text"]
    assert r["error"]["code"] == "server_error"


def test_oa19_background_resume_has_no_gaps_or_duplicates(mock) -> None:
    r = scenarios.run("OA-19", mock)
    assert r["dropped"] and r["resumed_from"] == 3
    assert r["sequence"] == list(range(len(r["sequence"])))
    assert r["last_event"] == "response.completed"
    assert r["text"] == "Claim #4411: water damage, kitchen ceiling."


def test_oa21_refusal_and_length(mock) -> None:
    r = scenarios.run("OA-21", mock)
    assert r["refusal"]["parsed"] is None and r["refusal"]["refusal"]
    assert r["length"]["exception"] == "LengthFinishReasonError"


def test_oa22_strict_lint() -> None:
    r = scenarios.run("OA-22", None)  # type: ignore[arg-type]
    assert len(r["loose"]) == 2 and r["fixed"] == []


def test_an17_400_vs_200(mock) -> None:
    r = scenarios.run("AN-17", mock)
    assert r["too_long"]["status"] == 400 and r["too_long"]["message"].startswith("prompt is too long")
    assert r["window_full"]["stop_reason"] == "model_context_window_exceeded"


def test_an22_text_after_tool_result(mock) -> None:
    r = scenarios.run("AN-22", mock)
    assert r["bad"]["output_tokens"] <= 2 and r["good"]["output_tokens"] > 0 and r["good"]["text"] == "ok"
    assert r["finding"]["cls"] == "tools.text_after_result" and r["lint_good"] is None


def test_an23_refusal_is_200(mock) -> None:
    r = scenarios.run("AN-23", mock)
    assert r["status"] == 200 and r["stop_reason"] == "refusal" and r["request_id"]


def test_an24_batch_reconcile_on_custom_id(mock) -> None:
    r = scenarios.run("AN-24", mock)
    rec = r["reconcile"]
    assert r["request_counts"]["succeeded"] == 6 and r["request_counts"]["errored"] == 2 and r["request_counts"]["expired"] == 2
    assert len(rec["resubmit"]) == 4 and rec["missing"] == [] and rec["positional_mismatches"] > 0


def test_tool_result_order_lint_variants() -> None:
    before = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "tool_result", "tool_use_id": "t"}]}]
    assert checks.tool_result_order(before).cls == "tools.text_before_result"
