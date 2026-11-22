"""Checks for failures that arrive as HTTP 200 — no error, wrong outcome.

These are the tickets status-code triage never sees: a tier silently downgraded, a bill landing
in the wrong org, a prompt cache that never hits.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    pack: str
    cls: str
    detail: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def service_tier(requested: str | None, served: str | None) -> Finding | None:
    """OA-13: over-ramped fast/priority requests are served at service_tier "default" (and billed so)."""
    if requested in (None, "auto", "default") or served in (None, requested):
        return None
    return Finding("OA-13", "billing.tier_downgraded",
                   f"requested service_tier={requested} but served {served}; billed at the served tier",
                   {"requested": requested, "served": served})


def org_mismatch(expected_org: str, served_org: str | None) -> Finding | None:
    """OA-15: openai-organization response header shows which org was billed."""
    if served_org is None or served_org == expected_org:
        return None
    return Finding("OA-15", "billing.wrong_org",
                   f"billed to {served_org}, expected {expected_org}; set OpenAI-Organization / OpenAI-Project",
                   {"expected": expected_org, "served": served_org})


def cache(usage: dict[str, int], *, cache_control_sent: bool, repeat: bool, pack: str = "AN-06") -> Finding | None:
    """AN-06 / AN-07: read the two cache fields the way support should."""
    created = usage.get("cache_creation_input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    if not cache_control_sent or read:
        return None
    if created == 0:
        return Finding(pack, "cache.below_minimum",
                       "cache_control sent but both cache fields are 0: prefix is under the model's minimum, "
                       "so nothing is cached and no error is returned",
                       {"cache_creation_input_tokens": created, "cache_read_input_tokens": read})
    if repeat:
        return Finding(pack, "cache.prefix_drift",
                       "repeat request wrote the cache again instead of reading it: the prefix changed",
                       {"cache_creation_input_tokens": created, "cache_read_input_tokens": read})
    return None


def first_prefix_change(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    """Where two requests' cacheable prefixes diverge, in invalidation order tools -> system -> messages."""
    for section in ("tools", "system", "messages"):
        if a.get(section) != b.get(section):
            if section == "tools" and sorted(map(repr, a.get("tools") or [])) == sorted(map(repr, b.get("tools") or [])):
                return "tools (same tools, different order)"
            return section
    return None


# ---------------------------------------------------------------- streams, structured output, batches

def stream_usage(final_usage: dict[str, Any] | None, include_usage_requested: bool) -> Finding | None:
    """OA-17: with include_usage the usage arrives in a last chunk whose `choices` is empty.

    If the stream is interrupted before that chunk, there is no usage to bill or log.
    """
    if not include_usage_requested:
        return Finding("OA-17", "stream.usage_not_requested",
                       "stream_options.include_usage was not set: streamed calls carry no token usage",
                       {"include_usage": False})
    if final_usage is None:
        return Finding("OA-17", "stream.usage_lost",
                       "include_usage was set but the stream ended before the usage chunk (interrupted)",
                       {"include_usage": True, "usage": None})
    return None


def stop_reason(provider: str, reason: str | None) -> Finding | None:
    """Stop reasons that look like outages to customers but are HTTP 200 by design."""
    table = {
        ("anthropic", "refusal"): ("AN-23", "model.refusal",
                                   "stop_reason=refusal is a normal 200: the model declined; not an outage"),
        ("anthropic", "model_context_window_exceeded"): ("AN-17", "context.window_full",
                                                         "input fit but input+max_tokens exceeded the window; output was cut"),
        ("anthropic", "max_tokens"): ("AN-17", "output.max_tokens", "output hit max_tokens"),
        ("openai", "length"): ("OA-21", "output.length", "finish_reason=length: output truncated at the token limit"),
        ("openai", "content_filter"): ("OA-21", "output.content_filter", "finish_reason=content_filter"),
    }
    hit = table.get((provider, reason or ""))
    if hit is None:
        return None
    return Finding(hit[0], hit[1], hit[2], {"provider": provider, "reason": reason})


def tool_result_order(messages: list[dict[str, Any]]) -> Finding | None:
    """AN-22: in the user turn that answers tool calls, tool_result blocks must come first; text after
    them is documented to produce near-empty replies."""
    if not messages or messages[-1].get("role") != "user" or not isinstance(messages[-1].get("content"), list):
        return None
    kinds = [b.get("type") for b in messages[-1]["content"] if isinstance(b, dict)]
    if "tool_result" not in kinds:
        return None
    first_text = next((i for i, k in enumerate(kinds) if k == "text"), None)
    last_result = max(i for i, k in enumerate(kinds) if k == "tool_result")
    if first_text is not None and first_text < last_result:
        return Finding("AN-22", "tools.text_before_result", "text placed before a tool_result block", {"kinds": kinds})
    if kinds[-1] == "text":
        return Finding("AN-22", "tools.text_after_result",
                       "text appended after tool_result blocks: send tool_result blocks alone", {"kinds": kinds})
    return None


def strict_schema_problems(schema: dict[str, Any], path: str = "$") -> list[str]:
    """OA-22: strict function/structured-output schemas need additionalProperties:false and every
    property listed in required (optional fields become nullable unions). Otherwise strict is not
    applied."""
    problems: list[str] = []
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        props = set((schema.get("properties") or {}).keys())
        missing = props - set(schema.get("required") or [])
        if missing:
            problems.append(f"{path}: not in required: {sorted(missing)} (make optional fields nullable instead)")
        for name, sub in (schema.get("properties") or {}).items():
            problems += strict_schema_problems(sub, f"{path}.{name}")
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        problems += strict_schema_problems(schema["items"], f"{path}[]")
    return problems


def batch_reconcile(request_ids: list[str], results: list[dict[str, Any]]) -> dict[str, Any]:
    """AN-24: results come back in any order. Match on custom_id; count what to resubmit."""
    by_id = {r["custom_id"]: r["result"]["type"] for r in results}
    positional_mismatch = sum(1 for req, res in zip(request_ids, results) if req != res["custom_id"])
    return {
        "succeeded": sorted(k for k, v in by_id.items() if v == "succeeded"),
        "resubmit": sorted(k for k, v in by_id.items() if v in ("errored", "expired")),
        "errored": sorted(k for k, v in by_id.items() if v == "errored"),
        "expired": sorted(k for k, v in by_id.items() if v == "expired"),
        "missing": sorted(set(request_ids) - set(by_id)),
        "positional_mismatches": positional_mismatch,
    }


# ---------------------------------------------------------------- agents and tools

def stateless_replay(prior_output: list[dict[str, Any]], next_input: list[dict[str, Any]]) -> Finding | None:
    """AG-08: with store=false, replay the whole prior `output` array (reasoning items included,
    with their encrypted content) — dropping reasoning items degrades tool use."""
    prior_reasoning = [i.get("id") for i in prior_output if i.get("type") == "reasoning"]
    replayed = {i.get("id") for i in next_input if isinstance(i, dict) and i.get("type") == "reasoning"}
    missing = [r for r in prior_reasoning if r not in replayed]
    if missing:
        return Finding("AG-08", "state.reasoning_dropped",
                       f"{len(missing)} reasoning item(s) from the previous output were not replayed",
                       {"missing": missing})
    return None


def connector_config_problems(cfg: dict[str, Any]) -> list[str]:
    """AG-15: claude.ai remote connectors are called from Anthropic's cloud."""
    from urllib.parse import urlparse

    problems: list[str] = []
    u = urlparse(cfg.get("url", ""))
    if u.scheme != "https":
        problems.append("url must be https")
    if u.port not in (None, 443):
        problems.append(f"port {u.port}: must be reachable on 443 from the public internet")
    host = (u.hostname or "")
    if host in ("localhost", "127.0.0.1") or host.endswith((".internal", ".local", ".corp")) or host.startswith(("10.", "192.168.")):
        problems.append(f"host {host} is not reachable from Anthropic's cloud")
    if cfg.get("own_oauth_client") and "https://claude.ai/api/mcp/auth_callback" not in (cfg.get("redirect_uris") or []):
        problems.append("own OAuth client must register redirect https://claude.ai/api/mcp/auth_callback")
    return problems
