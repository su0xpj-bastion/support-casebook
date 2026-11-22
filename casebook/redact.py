"""Secret redaction for anything that enters a Support Packet.

Key prefixes come from provider docs (Anthropic key table, OpenAI project/service-account
keys). Keys pasted into tickets are often wrapped across lines or JSON-escaped, so a key body
may contain a newline or a literal "\\n" between key characters.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections import Counter

_KEY_BODY = r"(?:[A-Za-z0-9_\-]|(?:\\n|\r?\n)(?=[A-Za-z0-9_\-]))"

# Order matters: most specific prefix first.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_admin_key", re.compile(r"sk-ant-admin01-" + _KEY_BODY + r"{16,}")),
    ("anthropic_compliance_key", re.compile(r"sk-ant-api01-" + _KEY_BODY + r"{16,}")),
    ("anthropic_api_key", re.compile(r"sk-ant-api03-" + _KEY_BODY + r"{16,}")),
    ("anthropic_other_key", re.compile(r"sk-ant-[a-z]+\d{2}-" + _KEY_BODY + r"{16,}")),
    ("openai_project_key", re.compile(r"sk-proj-" + _KEY_BODY + r"{16,}")),
    ("openai_service_account_key", re.compile(r"sk-svcacct-" + _KEY_BODY + r"{16,}")),
    ("openai_admin_key", re.compile(r"sk-admin-" + _KEY_BODY + r"{16,}")),
    ("openai_legacy_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]{16,}=*")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
]

SECRET_HEADERS = {"authorization", "x-api-key", "api-key", "openai-api-key", "cookie", "set-cookie"}
# Headers seen live that carry a base64 copy of the error body (OpenAI x-error-json, captured 2026-11-19).
# Plain-text regexes cannot see a secret inside base64, so these are decoded and scanned.
BASE64_HEADERS = {"x-error-json"}


def redact(text: str) -> tuple[str, Counter[str]]:
    """Return (redacted_text, counts_by_kind)."""
    counts: Counter[str] = Counter()
    for kind, pattern in PATTERNS:
        def _sub(match: re.Match[str], kind: str = kind) -> str:
            counts[kind] += 1
            return f"[REDACTED:{kind}]"

        text = pattern.sub(_sub, text)
    return text, counts


def redact_headers(headers: dict[str, str]) -> tuple[dict[str, str], int]:
    """Drop secret-bearing header values; redact secrets inside the rest."""
    out: dict[str, str] = {}
    n = 0
    for name, value in headers.items():
        if name.lower() in SECRET_HEADERS:
            out[name] = "[REDACTED:header]"
            n += 1
            continue
        if name.lower() in BASE64_HEADERS:
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8", "replace")
            except (binascii.Error, ValueError):
                decoded = ""
            _, inner = redact(decoded)
            if inner:
                out[name] = "[REDACTED:base64-header]"
                n += sum(inner.values())
                continue
        clean, counts = redact(value)
        n += sum(counts.values())
        out[name] = clean
    return out, n
