"""Look-alike discrimination without the network: same status, different fix."""
from __future__ import annotations

import pytest

from casebook import packet as pk
from casebook import rules


def oa(status, code=None, etype=None):
    return pk.from_http("openai", status, {"x-request-id": "r"},
                        {"error": {"message": "m", "type": etype, "param": None, "code": code}})


def an(status, etype, code=None, msg="m", retry_after=None):
    err = {"type": etype, "message": msg}
    if code:
        err["details"] = {"error_code": code}
    h = {"request-id": "req_1"}
    if retry_after is not None:
        h["retry-after"] = str(retry_after)
    return pk.from_http("anthropic", status, h, {"type": "error", "error": err, "request_id": "req_1"})


@pytest.mark.parametrize("packet,cls,retry", [
    (oa(429, "slow_down", "rate_limit_error"), "limits.ramp", "Y"),
    (oa(429, None, "requests"), "limits.rate", "Y"),
    (oa(429, "credit_balance_exhausted", "insufficient_quota"), "billing.credits_exhausted", "N"),
    (oa(429, "organization_spend_limit_exceeded", "insufficient_quota"), "billing.spend_limit", "N"),
    (oa(429, None, "insufficient_quota"), "billing.quota", "N"),
    (oa(503, "server_is_overloaded", "service_unavailable_error"), "provider.capacity", "Y"),
    (oa(401), "auth.invalid", "N"),
])
def test_openai_429_family(packet, cls, retry) -> None:
    v = rules.classify(packet)
    assert (v.cls, v.retryable) == (cls, retry)


@pytest.mark.parametrize("packet,cls,retry", [
    (an(429, "rate_limit_error", "enforced_spend_limit_reached"), "billing.tier_spend_cap", "N"),
    (an(429, "rate_limit_error", "ramp_limit_exceeded", retry_after=3), "limits.ramp", "Y"),
    (an(429, "rate_limit_error", retry_after=3), "limits.rate", "Y"),
    (an(429, "rate_limit_error"), "billing.tier_spend_cap?", "C"),
    (an(400, "invalid_request_error", msg="You have reached your specified API usage limits."), "billing.self_set_spend_limit", "N"),
    (an(400, "invalid_request_error", msg="messages: field required"), "request.invalid", "N"),
    (an(529, "overloaded_error"), "provider.capacity", "Y"),
    (an(402, "billing_error"), "billing.payment", "N"),
    (an(413, "request_too_large"), "request.too_large", "N"),
    (an(504, "timeout_error"), "provider.timeout", "C"),
])
def test_anthropic_look_alikes(packet, cls, retry) -> None:
    v = rules.classify(packet)
    assert (v.cls, v.retryable) == (cls, retry)


def test_unknown_error_code_falls_back_to_type() -> None:
    # SDK guidance: treat an unrecognized error_code as the bare type.
    v = rules.classify(an(429, "rate_limit_error", "some_future_code", retry_after=1))
    assert v.cls == "limits.rate"


def test_no_non_retryable_class_is_marked_retryable() -> None:
    for p in (oa(429, "project_spend_limit_exceeded"), oa(429, "organization_usage_limit_exceeded"),
              an(429, "rate_limit_error", "enforced_spend_limit_reached", retry_after=5)):
        assert rules.classify(p).retryable == "N"
