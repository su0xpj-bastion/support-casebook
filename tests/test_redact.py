from __future__ import annotations

from casebook.redact import redact, redact_headers

BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def test_red_01_every_documented_prefix_is_redacted() -> None:
    text = (
        f"api sk-ant-api03-{BODY} admin sk-ant-admin01-{BODY} compliance sk-ant-api01-{BODY} "
        f"proj sk-proj-{BODY} auth: Bearer {BODY}.{BODY} "
        f"jwt eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjaS1ib3QifQ.c2lnbmF0dXJlLXZhbHVl"
    )
    out, counts = redact(text)
    assert sum(counts.values()) == 6
    assert set(counts) == {"anthropic_api_key", "anthropic_admin_key", "anthropic_compliance_key",
                           "openai_project_key", "bearer_token", "jwt"}
    assert BODY not in out


def test_red_01b_specific_prefix_wins_over_generic() -> None:
    _, counts = redact(f"sk-ant-admin01-{BODY}")
    assert counts == {"anthropic_admin_key": 1}


def test_red_02_key_wrapped_across_lines_and_json_escaped() -> None:
    wrapped = f"key=sk-ant-api03-{BODY[:14]}\n{BODY[14:]} end"
    escaped = '{"key": "sk-proj-' + BODY[:10] + "\\n" + BODY[10:] + '"}'
    for text in (wrapped, escaped):
        out, counts = redact(text)
        assert sum(counts.values()) == 1, text
        assert BODY[14:] not in out and BODY[10:] not in out


def test_red_03_lookalikes_are_not_redacted() -> None:
    text = "we use sk-learn and scikit; request req_011CVabcdEFGH12345678 failed; sk-short"
    out, counts = redact(text)
    assert not counts
    assert "req_011CVabcdEFGH12345678" in out


def test_red_04_secret_headers_dropped_others_kept() -> None:
    hdrs, n = redact_headers({"Authorization": f"Bearer {BODY}", "x-api-key": BODY, "request-id": "req_1"})
    assert n == 2
    assert hdrs["request-id"] == "req_1"
    assert BODY not in str(hdrs)


def test_red_05_base64_error_header_is_decoded_and_scanned() -> None:
    import base64

    leaky = base64.b64encode(f'{{"error": {{"message": "bad key sk-proj-{BODY}"}}}}'.encode()).decode()
    clean = base64.b64encode(b'{"error": {"message": "Incorrect API key provided: sk-proj-****0000"}}').decode()
    hdrs, n = redact_headers({"x-error-json": leaky})
    assert hdrs["x-error-json"] == "[REDACTED:base64-header]" and n == 1
    hdrs, n = redact_headers({"x-error-json": clean})
    assert hdrs["x-error-json"] == clean and n == 0
