"""Live captures against the real APIs (provenance: live-captured).

Two tiers:
  free   no account needed: requests are deliberately unauthenticated (a fake key) or rejected at the
         edge, so they cost nothing and touch no one's data. OA-07, AN-13, AN-16.
  keyed  needs the user's own key and, for some packs, a dashboard setting first (pack.yaml
         `live_setup`). Never run without --keyed.

Every capture is written to faults/<provider>/<pack>/live/<UTC date>-python.json with the real
status, headers (secrets redacted), body, SDK versions and a field-by-field diff against
expected.json. A difference is a finding, not a failure.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import anthropic
import openai

from casebook import packet as pk
from casebook import packs, rules
from casebook.redact import redact, redact_headers
from casebook.repro import request_kwargs

FAKE_OPENAI_KEY = "sk-proj-casebookFAKEcasebookFAKEcasebookFAKE0000"
FAKE_ANTHROPIC_KEY = "sk-ant-api03-casebookFAKEcasebookFAKEcasebookFAKE0000"
OVERSIZE_BYTES = 33 * 1024 * 1024  # just over the documented 32 MB Messages API request limit

FREE = ("OA-07", "AN-13", "AN-16")


def _call(pack_id: str, key_override: str | None) -> tuple[Any, Any]:
    """Return (exception or None, response or None) from one real-API call with retries off."""
    pack = packs.get(pack_id)
    provider = pack.provider
    kw = request_kwargs(provider, pack.meta.get("request"))
    if pack_id == "AN-16":
        kw["messages"] = [{"role": "user", "content": "x" * OVERSIZE_BYTES}]
    if provider == "openai":
        client = openai.OpenAI(api_key=key_override or os.environ["OPENAI_API_KEY"], max_retries=0, timeout=60)
        call = client.chat.completions.with_raw_response.create
    else:
        client = anthropic.Anthropic(api_key=key_override or os.environ["ANTHROPIC_API_KEY"], max_retries=0, timeout=120)
        call = client.messages.with_raw_response.create
    try:
        return None, call(**kw)
    except (openai.APIStatusError, anthropic.APIStatusError) as e:
        return e, None


def capture(pack_id: str, *, keyed: bool = False) -> dict[str, Any]:
    pack = packs.get(pack_id)
    if pack_id not in FREE and not keyed:
        raise PermissionError(f"{pack_id} needs your own API key (and maybe a dashboard setting); pass keyed=True")
    key = None
    if pack_id in FREE:
        key = FAKE_OPENAI_KEY if pack.provider == "openai" else FAKE_ANTHROPIC_KEY
    exc, raw = _call(pack_id, key)
    resp = exc.response if exc is not None else raw.http_response
    headers, _ = redact_headers({k.lower(): v for k, v in resp.headers.items()})
    text, _ = redact(resp.text[:4000])
    try:
        body: Any = json.loads(text)
    except ValueError:
        body = text
    packet = pk.from_sdk_error(pack.provider, exc) if exc is not None else None
    verdict = rules.classify(packet).as_dict() if packet else None
    cap = {
        "pack": pack_id,
        "captured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": "live-captured",
        "tier": "free" if pack_id in FREE else "keyed",
        "sdk": {"openai": openai.__version__, "anthropic": anthropic.__version__},
        "exception": f"{type(exc).__module__.split('.')[0]}.{type(exc).__name__}" if exc else None,
        "status": resp.status_code,
        "x_should_retry": resp.headers.get("x-should-retry"),
        "headers": headers,
        "body": body,
        "verdict": verdict,
        "diff": diff(pack, resp.status_code, body),
    }
    out_dir = pack.path / "live"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{cap['captured_utc'][:10]}-python.json").write_text(json.dumps(cap, indent=2) + "\n", encoding="utf-8")
    return cap


def diff(pack: packs.Pack, status: int, body: Any) -> list[dict[str, Any]]:
    """Field-by-field comparison of the live response with expected.json."""
    exp = pack.expected
    rows = [_row("status", exp.get("status"), status)]
    exp_body = exp.get("body") or {}
    if pack.provider == "openai":
        e_err, l_err = exp_body.get("error", {}), (body.get("error", {}) if isinstance(body, dict) else {})
        for f in ("type", "code", "message"):
            rows.append(_row(f"error.{f}", e_err.get(f), l_err.get(f)))
    elif exp.get("mode") == "raw":
        rows.append(_row("content-type html", True, isinstance(body, str) and "<html" in body.lower()))
    else:
        e_err, l_err = exp_body.get("error", {}), (body.get("error", {}) if isinstance(body, dict) else {})
        for f in ("type", "message"):
            rows.append(_row(f"error.{f}", e_err.get(f), l_err.get(f)))
        rows.append(_row("request_id in body", "request_id" in exp_body, isinstance(body, dict) and "request_id" in body))
    return rows


def _row(field: str, expected: Any, live: Any) -> dict[str, Any]:
    return {"field": field, "expected": expected, "live": live, "match": expected == live}
