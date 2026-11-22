"""Support Packet: the one evidence object every casebook component reads and writes.

Field sources (provider docs):
  OpenAI     x-request-id, X-Client-Request-Id (client-set, ASCII, <=512 chars), openai-organization,
             openai-processing-ms, openai-version, x-ratelimit-*; body {"error": {message,type,param,code}}
  Anthropic  request-id (mirrored as body request_id), anthropic-organization-id,
             anthropic-workspace-id, anthropic-ratelimit-* / anthropic-priority-*, retry-after;
             body {"type":"error","error":{type,message,details:{error_code}},"request_id"}
  AWS        Claude Platform on AWS returns x-amzn-requestid AND request-id; Anthropic tickets need request-id.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any

from casebook.redact import redact, redact_headers

SCHEMA_PATH = Path(__file__).parent / "schema" / "packet.schema.json"
PACKET_VERSION = 1
_case_seq = count(1)

RATE_HEADER_PREFIXES = ("x-ratelimit-", "anthropic-ratelimit-", "anthropic-priority-", "anthropic-fast-")
KNOWN_CODES = (
    "credit_balance_exhausted", "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded", "slow_down", "server_is_overloaded", "insufficient_quota",
    "enforced_spend_limit_reached", "ramp_limit_exceeded", "overloaded_error", "rate_limit_error",
    "invalid_request_error", "authentication_error", "permission_error", "billing_error",
    "invalid_api_key", "unsupported_country_region_territory", "rate_limit_exceeded", "timeout_error",
    "request_too_large",
)
ANTHROPIC_DETAIL_CODES = ("enforced_spend_limit_reached", "ramp_limit_exceeded")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lower(headers: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}


def parse_retry_after(headers: dict[str, str]) -> float | None:
    """Seconds to wait, from retry-after-ms or retry-after (delta-seconds form only)."""
    ms = headers.get("retry-after-ms")
    if ms is not None:
        try:
            return float(ms) / 1000.0
        except ValueError:
            pass
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form: not produced by either provider's documented examples


def validate_client_request_id(value: str) -> list[str]:
    """OpenAI X-Client-Request-Id: ASCII only, at most 512 characters, else the API returns 400."""
    problems = []
    if len(value) > 512:
        problems.append(f"X-Client-Request-Id is {len(value)} chars; max is 512 (API returns 400)")
    if not value.isascii():
        problems.append("X-Client-Request-Id must be ASCII (API returns 400)")
    return problems


def _error_fields(provider: str, body: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": None, "code": None, "details_error_code": None, "message": None}
    if not isinstance(body, dict):
        return out
    err = body.get("error")
    if not isinstance(err, dict):
        return out
    out["type"] = err.get("type")
    out["message"] = err.get("message")
    if provider == "openai":
        out["code"] = err.get("code")
    details = err.get("details")
    if isinstance(details, dict) and isinstance(details.get("error_code"), str):
        out["details_error_code"] = details["error_code"]
    return out


def from_http(
    provider: str,
    status: int | None,
    headers: Any,
    body: Any,
    *,
    endpoint: str | None = None,
    model: str | None = None,
    sdk: dict[str, Any] | None = None,
    request_headers: Any = None,
    request_body: Any = None,
    channel: str = "sdk",
    provenance: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    h = _lower(headers)
    if isinstance(body, (bytes, str)) and h.get("content-type", "").startswith("text/html"):
        body = None  # a CDN/proxy page, not an API error envelope
    rq = _lower(request_headers)
    clean_headers, header_redactions = redact_headers(h)
    err = _error_fields(provider, body)
    redactions = header_redactions
    if err["message"]:
        err["message"], counts = redact(err["message"])
        redactions += sum(counts.values())

    warnings: list[str] = []
    if provider == "openai":
        provider_rid = h.get("x-request-id")
        org = h.get("openai-organization")
        proj = rq.get("openai-project")
    else:
        provider_rid = h.get("request-id")
        org = h.get("anthropic-organization-id")
        proj = h.get("anthropic-workspace-id")
        body_rid = body.get("request_id") if isinstance(body, dict) else None
        if body_rid and provider_rid and body_rid != provider_rid:
            warnings.append(f"body request_id {body_rid} != header request-id {provider_rid}")
        provider_rid = provider_rid or body_rid

    client_rid = rq.get("x-client-request-id")
    if client_rid:
        warnings.extend(validate_client_request_id(client_rid))
    cloud_rid = h.get("x-amzn-requestid")
    if cloud_rid:
        warnings.append("two request ids present: use request-id for Anthropic tickets, x-amzn-requestid for AWS/CloudTrail")
    if not provider_rid:
        warnings.append("no provider request id: send UTC timestamp, org/project or workspace, and a client request id")
    if status is not None and status >= 400 and err["type"] is None and body is None:
        warnings.append("error body is not an API error envelope: likely rejected before the API (CDN/proxy)")
    stream_error = status == 200 and err["type"] is not None
    if stream_error:
        warnings.append("error arrived mid-stream after HTTP 200: status-based retry logic will not see it")
    req_meta = _request_meta(request_body)

    sdk_info = dict(sdk or {})
    if provider == "anthropic" and rq.get("anthropic-version"):
        sdk_info.setdefault("api_version", rq["anthropic-version"])

    return {
        "packet_version": PACKET_VERSION,
        "case_id": case_id or f"CB-{datetime.now(timezone.utc):%Y}-{next(_case_seq):04d}",
        "received_utc": _now(),
        "channel": channel,
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "sdk": sdk_info or None,
        "org_id": org,
        "project_or_workspace": proj,
        "request_ids": {"provider": provider_rid, "client": client_rid, "cloud": cloud_rid},
        "http": {"status": status, "retry_after": parse_retry_after(h)},
        "error": err,
        "stream_error": stream_error,
        "request_meta": req_meta,
        "rate_headers": {k: v for k, v in clean_headers.items() if k.startswith(RATE_HEADER_PREFIXES)},
        "provenance": provenance,
        "redactions": redactions,
        "warnings": warnings,
    }


def _request_meta(request_body: Any) -> dict[str, Any] | None:
    """Request-side fields that change the diagnosis (never the prompt itself)."""
    if isinstance(request_body, (bytes, bytearray)):
        try:
            request_body = json.loads(request_body or b"{}")
        except ValueError:
            return None
    if not isinstance(request_body, dict):
        return None
    keep = ("model", "service_tier", "stream", "max_tokens", "max_completion_tokens", "tool_choice")
    meta = {k: request_body[k] for k in keep if k in request_body}
    return meta or None


def from_client_error(provider: str, exc: Any, kind: str, **kw: Any) -> dict[str, Any]:
    """Packet for a failure with no HTTP response (timeout, connection reset, DNS).

    There is no provider request id by definition, so the client request id and the UTC
    timestamp are the only way support can find the call.
    """
    req = getattr(exc, "request", None)
    p = from_http(provider, None, {}, None,
                  endpoint=str(req.url.path) if req is not None else None,
                  request_headers=req.headers if req is not None else None,
                  request_body=req.content if req is not None else None, **kw)
    p["error"]["type"] = kind
    p["error"]["message"] = str(exc)
    if not p["request_ids"]["client"]:
        p["warnings"].append("no X-Client-Request-Id was sent: set one so support can trace calls that never return")
    return p


def from_sdk_error(provider: str, exc: Any, **kw: Any) -> dict[str, Any]:
    """Build a packet from an openai/anthropic APIStatusError.

    Reads the raw response body: the OpenAI SDK sets exc.body to the inner `error` object,
    the Anthropic SDK to the whole envelope, so exc.body is not comparable across providers.
    """
    resp = exc.response
    req = getattr(resp, "request", None)
    body = _try_json(resp)
    if body is None and isinstance(exc.body, dict):
        body = exc.body if "error" in exc.body else {"error": exc.body}
    return from_http(
        provider,
        exc.status_code,
        resp.headers,
        body,
        endpoint=str(req.url.path) if req is not None else None,
        request_headers=req.headers if req is not None else None,
        request_body=req.content if req is not None else None,
        **kw,
    )


def _try_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — body may be empty, non-JSON (CDN 413), or an already-consumed stream
        return None


_RID = re.compile(r"\breq_[A-Za-z0-9]{8,}\b")
API_STATUSES = {400, 401, 402, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504, 529}
_STATUS = re.compile(r"\b(4\d\d|5\d\d)\b")


def from_text(text: str, *, channel: str = "ticket", provider: str | None = None) -> dict[str, Any]:
    """Minimal packet from free text: redact first, then pull ids, statuses and known codes."""
    clean, counts = redact(text)
    lowered = clean.lower()
    http_marked = [int(x) for x in re.findall(r"\bHTTP\s*(\d{3})\b", clean)]
    statuses = sorted({int(s) for s in _STATUS.findall(clean)})
    if http_marked:                       # 'HTTP 429' beats numbers like 'Limit 500' elsewhere in the text
        statuses = sorted(set(http_marked))
    elif len(statuses) > 1:
        statuses = [x for x in statuses if x in API_STATUSES] or statuses
    codes = [c for c in KNOWN_CODES if c in lowered]
    rids = _RID.findall(clean)
    warnings = [] if rids else ["no request id in ticket: ask for request-id / x-request-id and a UTC timestamp"]
    # Put each mentioned code in the field the provider actually uses for it.
    err: dict[str, Any] = {"type": None, "code": None, "details_error_code": None, "message": None}
    for c in codes:
        if c in ANTHROPIC_DETAIL_CODES:
            err["details_error_code"] = err["details_error_code"] or c
        elif c.endswith("_error") or c == "insufficient_quota":
            err["type"] = err["type"] or c
        else:
            err["code"] = err["code"] or c
    return {
        "packet_version": PACKET_VERSION,
        "case_id": f"CB-{datetime.now(timezone.utc):%Y}-{next(_case_seq):04d}",
        "received_utc": _now(),
        "channel": channel,
        "provider": provider,
        "endpoint": None,
        "model": None,
        "sdk": None,
        "org_id": None,
        "project_or_workspace": None,
        "request_ids": {"provider": rids[0] if rids else None, "client": None, "cloud": None},
        "http": {"status": statuses[0] if len(statuses) == 1 else None, "retry_after": None},
        "error": err,
        "rate_headers": {},
        "provenance": None,
        "redactions": sum(counts.values()),
        "warnings": warnings,
        "text": clean,
        "mentioned": {"statuses": statuses, "codes": codes, "request_ids": rids},
    }


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)
