from __future__ import annotations

import anthropic
import jsonschema
import openai

from casebook import packet as pk

SCHEMA = pk.load_schema()


def _valid(p: dict) -> None:
    jsonschema.validate({k: v for k, v in p.items() if k not in ("text", "mentioned")}, SCHEMA)


def test_pkt_01_openai_headers() -> None:
    p = pk.from_http(
        "openai", 429,
        {"x-request-id": "req_abc", "openai-organization": "org-1", "x-ratelimit-remaining-tokens": "0",
         "retry-after": "2", "authorization": "Bearer secretsecretsecret"},
        {"error": {"message": "m", "type": "rate_limit_error", "param": None, "code": "slow_down"}},
        request_headers={"OpenAI-Project": "proj_9", "X-Client-Request-Id": "cli-1"},
    )
    _valid(p)
    assert p["request_ids"] == {"provider": "req_abc", "client": "cli-1", "cloud": None}
    assert p["org_id"] == "org-1" and p["project_or_workspace"] == "proj_9"
    assert p["http"]["retry_after"] == 2.0
    assert p["rate_headers"] == {"x-ratelimit-remaining-tokens": "0"}
    assert p["error"]["code"] == "slow_down"


def test_pkt_02_anthropic_success_headers_via_sdk(mock) -> None:
    client = anthropic.Anthropic(api_key="sk-ant-test", base_url=mock.url, max_retries=0)
    raw = client.messages.with_raw_response.create(
        model="claude-sonnet-5-5", max_tokens=8, messages=[{"role": "user", "content": "hi"}])
    p = pk.from_http("anthropic", raw.status_code, raw.headers, None,
                     request_headers=raw.http_request.headers)
    _valid(p)
    assert p["request_ids"]["provider"].startswith("req_")
    assert p["request_ids"]["provider"] == raw.parse()._request_id
    assert p["org_id"] and p["project_or_workspace"].startswith("wrkspc_")
    assert p["sdk"]["api_version"]  # anthropic-version actually sent by the SDK


def test_pkt_02b_openai_success_request_id_via_sdk(mock) -> None:
    client = openai.OpenAI(api_key="sk-test", base_url=f"{mock.url}/v1", max_retries=0)
    resp = client.chat.completions.create(model="gpt-6-luna", messages=[{"role": "user", "content": "hi"}])
    assert resp._request_id and resp._request_id.startswith("req_")


def test_pkt_03_aws_two_request_ids() -> None:
    p = pk.from_http("anthropic", 500, {"request-id": "req_011X", "x-amzn-requestid": "aws-1"},
                     {"type": "error", "error": {"type": "api_error", "message": "x"}, "request_id": "req_011X"})
    _valid(p)
    assert p["request_ids"] == {"provider": "req_011X", "client": None, "cloud": "aws-1"}
    assert any("request-id for Anthropic tickets" in w for w in p["warnings"])


def test_pkt_03b_body_header_request_id_mismatch_warns() -> None:
    p = pk.from_http("anthropic", 500, {"request-id": "req_A"},
                     {"type": "error", "error": {"type": "api_error", "message": "x"}, "request_id": "req_B"})
    assert any("!=" in w for w in p["warnings"])


def test_pkt_04_client_request_id_rules() -> None:
    assert pk.validate_client_request_id("ok-123") == []
    assert len(pk.validate_client_request_id("x" * 513)) == 1
    assert len(pk.validate_client_request_id("café")) == 1


def test_pkt_05_missing_request_id_tells_support_what_to_ask_for() -> None:
    p = pk.from_http("openai", 504, {}, None)
    assert any("UTC timestamp" in w for w in p["warnings"])


def test_pkt_06_message_secrets_redacted_and_counted() -> None:
    p = pk.from_http("openai", 401, {"x-request-id": "r"},
                     {"error": {"message": "Incorrect API key provided: sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA",
                                "type": "invalid_request_error", "code": "invalid_api_key"}})
    assert "AAAAAAAA" not in p["error"]["message"] and p["redactions"] == 1


def test_pkt_07_from_text_ticket() -> None:
    ticket = ("Since 00:00 UTC every call fails with 429 enforced_spend_limit_reached, request req_011CVabcdEFGH1234. "
              "Our key is sk-ant-api03-A1b2C3d4E5f6G7h8I9j0K1l2 if you need it.")
    p = pk.from_text(ticket, provider="anthropic")
    _valid(p)
    assert p["redactions"] == 1 and "A1b2C3d4" not in p["text"]
    assert p["request_ids"]["provider"] == "req_011CVabcdEFGH1234"
    assert p["http"]["status"] == 429 and p["error"]["details_error_code"] == "enforced_spend_limit_reached"
    from casebook import rules
    v = rules.classify(p)
    assert (v.cls, v.retryable) == ("billing.tier_spend_cap", "N")


def test_pkt_07b_from_text_openai_code_lands_in_error_code() -> None:
    p = pk.from_text("getting 429 project_spend_limit_exceeded since noon", provider="openai")
    assert p["error"]["code"] == "project_spend_limit_exceeded"
    assert p["warnings"]  # no request id -> tells support what to ask for


def test_pkt_08_retry_after_ms_wins() -> None:
    assert pk.parse_retry_after({"retry-after-ms": "250", "retry-after": "3"}) == 0.25
    assert pk.parse_retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
