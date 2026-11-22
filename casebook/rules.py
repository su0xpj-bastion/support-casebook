"""Deterministic triage: Support Packet -> verdict.

Branches on documented machine-readable fields first (status, error.type, error.code,
error.details.error_code, presence of retry-after, request service_tier). Message text is used
only where the docs give no code — and the verdict says so, because the Anthropic SDK itself
warns that error.message wording "can change, so don't match on it".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

OPENAI_DOCS = "https://developers.openai.com/api/docs/guides/error-codes"
OPENAI_FLEX = "https://developers.openai.com/api/docs/guides/flex-processing"
OPENAI_REF = "https://developers.openai.com/api/reference/overview"
ANTHROPIC_ERR = "https://platform.claude.com/docs/en/api/errors"
ANTHROPIC_RL = "https://platform.claude.com/docs/en/api/rate-limits"


@dataclass(frozen=True)
class Verdict:
    cls: str
    retryable: str  # "Y" retry with backoff, "N" fix config/billing first, "C" conditional
    owner: str  # who must act: customer | customer-admin | provider | network
    basis: str  # the evidence field the decision rests on
    confidence: str  # "code" | "message-prefix" | "header-heuristic" | "status-only" | "client-side"
    doc: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify(p: dict[str, Any]) -> Verdict:
    provider = p.get("provider")
    status = (p.get("http") or {}).get("status")
    err = p.get("error") or {}
    if status is None and err.get("type") in ("client_timeout", "connection_error"):
        return _client_side(err["type"], provider)
    if provider == "openai":
        return _openai(status, err, p.get("request_meta") or {})
    if provider == "anthropic":
        return _anthropic(status, err, (p.get("http") or {}).get("retry_after"), p.get("rate_headers") or {})
    return Verdict("unknown", "C", "support", "provider unknown", "status-only", "")


def _client_side(kind: str, provider: str | None) -> Verdict:
    doc = OPENAI_REF if provider == "openai" else ANTHROPIC_ERR
    if kind == "client_timeout":
        return Verdict("client.timeout", "C", "customer",
                       "no HTTP response before the client timeout", "client-side", doc)
    return Verdict("network.connection", "C", "network", "connection failed before a response", "client-side", doc)


_OA_BILLING = {
    "credit_balance_exhausted": "billing.credits_exhausted",
    "organization_spend_limit_exceeded": "billing.spend_limit",
    "project_spend_limit_exceeded": "billing.spend_limit",
    "organization_usage_limit_exceeded": "billing.usage_limit",
}


def _openai(status: int | None, err: dict[str, Any], req: dict[str, Any]) -> Verdict:
    code = err.get("code")
    etype = err.get("type")
    msg = err.get("message") or ""
    if status == 429:
        if code in _OA_BILLING:
            return Verdict(_OA_BILLING[code], "N", "customer-admin", f"error.code={code}", "code", OPENAI_DOCS)
        if code == "slow_down":
            return Verdict("limits.ramp", "Y", "customer", "error.code=slow_down", "code", OPENAI_DOCS)
        if etype == "insufficient_quota":
            return Verdict("billing.quota", "N", "customer-admin", "error.type=insufficient_quota", "code", OPENAI_DOCS)
        if req.get("service_tier") == "flex":
            return Verdict("capacity.flex", "Y", "provider",
                           "429 on a service_tier=flex request (not charged)", "code", OPENAI_FLEX)
        return Verdict("limits.rate", "Y", "customer", "status=429, no billing code", "status-only", OPENAI_DOCS)
    if status == 503 and code == "server_is_overloaded":
        return Verdict("provider.capacity", "Y", "provider", "error.code=server_is_overloaded", "code", OPENAI_DOCS)
    if status is not None and status >= 500:
        return Verdict("provider.error", "Y", "provider", f"status={status}", "status-only", OPENAI_DOCS)
    if status == 408 and req.get("service_tier") == "flex":
        return Verdict("capacity.flex_timeout", "Y", "provider", "408 on a service_tier=flex request", "code", OPENAI_FLEX)
    if status == 401:
        if "IP" in msg and "not authorized" in msg:
            return Verdict("auth.ip_allowlist", "N", "customer-admin", "401 + IP allowlist message", "message-prefix", OPENAI_DOCS)
        return Verdict("auth.invalid", "N", "customer", f"status=401 error.code={code}", "code" if code else "status-only", OPENAI_DOCS)
    if status == 403:
        if "Country, region, or territory not supported" in msg:
            return Verdict("auth.region", "N", "customer", "403 + unsupported-region message", "message-prefix", OPENAI_DOCS)
        return Verdict("auth.forbidden", "N", "customer-admin", "status=403", "status-only", OPENAI_DOCS)
    if status == 400:
        if "Invalid `service_tier`" in msg or "Invalid service_tier" in msg:
            return Verdict("config.service_tier", "N", "customer-admin",
                           "400 + service_tier message (project restricts tiers)", "message-prefix", OPENAI_DOCS)
        return Verdict("request.invalid", "N", "customer", "status=400", "status-only", OPENAI_DOCS)
    return Verdict("unknown", "C", "support", f"status={status}", "status-only", OPENAI_DOCS)


def _anthropic(status: int | None, err: dict[str, Any], retry_after: float | None,
               rate: dict[str, str]) -> Verdict:
    etype = err.get("type")
    code = err.get("details_error_code")
    msg = err.get("message") or ""
    if status == 429:
        if code == "enforced_spend_limit_reached":
            return Verdict("billing.tier_spend_cap", "N", "customer-admin",
                           "error.details.error_code=enforced_spend_limit_reached (no retry-after)", "code", ANTHROPIC_RL)
        if code == "ramp_limit_exceeded":
            return Verdict("limits.ramp", "Y", "customer", "error.details.error_code=ramp_limit_exceeded", "code", ANTHROPIC_RL)
        if retry_after is None:
            # Docs: the spend-cap 429 is the one without retry-after. Without a code, treat as suspect.
            return Verdict("billing.tier_spend_cap?", "C", "customer-admin",
                           "status=429 and no retry-after", "status-only", ANTHROPIC_RL)
        if _workspace_binding(rate):
            return Verdict("limits.workspace", "Y", "customer-admin",
                           "anthropic-ratelimit-tokens-limit < anthropic-ratelimit-input-tokens-limit",
                           "header-heuristic", ANTHROPIC_RL)
        return Verdict("limits.rate", "Y", "customer", "status=429 with retry-after", "status-only", ANTHROPIC_RL)
    if status == 400 and etype == "invalid_request_error" and msg.startswith(
        ("You have reached your specified API usage limits", "You have reached your specified workspace API usage limits")
    ):
        return Verdict("billing.self_set_spend_limit", "N", "customer-admin",
                       "400 + message prefix (no documented code)", "message-prefix", ANTHROPIC_RL)
    if status == 529 or etype == "overloaded_error":
        basis = "SSE error event after HTTP 200" if status == 200 else "error.type=overloaded_error"
        return Verdict("provider.capacity", "Y", "provider", basis, "code", ANTHROPIC_ERR)
    if status == 504 or etype == "timeout_error":
        return Verdict("provider.timeout", "C", "provider",
                       "error.type=timeout_error (retrying a long non-streaming call can bill again; stream instead)",
                       "code", ANTHROPIC_ERR)
    if status == 500:
        return Verdict("provider.error", "Y", "provider", "error.type=api_error", "code", ANTHROPIC_ERR)
    if status == 401 and msg.startswith("CORS requests are not allowed"):
        return Verdict("auth.cors_blocked", "N", "customer", "401 + CORS policy message", "message-prefix", ANTHROPIC_ERR)
    if status == 413 and etype is None:
        return Verdict("request.too_large", "N", "customer",
                       "413 with no API error envelope (rejected at the edge; no request-id)", "status-only", ANTHROPIC_ERR)
    fixed = {
        400: ("request.invalid", "customer"), 401: ("auth.invalid", "customer"),
        402: ("billing.payment", "customer-admin"), 403: ("auth.forbidden", "customer-admin"),
        404: ("request.not_found", "customer"), 413: ("request.too_large", "customer"),
    }
    if status in fixed:
        cls, owner = fixed[status]
        return Verdict(cls, "N", owner, f"status={status} error.type={etype}", "code" if etype else "status-only", ANTHROPIC_ERR)
    if status == 409:
        return Verdict("request.conflict", "C", "customer", "status=409", "code", ANTHROPIC_ERR)
    return Verdict("unknown", "C", "support", f"status={status}", "status-only", ANTHROPIC_ERR)


def _workspace_binding(rate: dict[str, str]) -> bool:
    """Docs: anthropic-ratelimit-tokens-* report the most restrictive limit in effect (e.g. a
    workspace limit). If it is below the org's input-token limit, a workspace cap is binding."""
    try:
        return int(rate["anthropic-ratelimit-tokens-limit"]) < int(rate["anthropic-ratelimit-input-tokens-limit"])
    except (KeyError, ValueError):
        return False
