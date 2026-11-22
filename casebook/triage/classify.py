"""Ticket triage: rules first, one LLM call as fallback, retryability never decided by the model.

  rules     redact -> Support Packet from text -> casebook.rules on documented codes, plus a short list
            of documented message phrases. A bare status (e.g. '429') is NOT enough: ask for more info.
  llm       redacted ticket -> one Chat Completions call -> {"class", "evidence": [verbatim quotes]}
  combined  rules when they're confident; otherwise the LLM, guarded: a class the LLM proposes that
            would tell the customer to retry needs an explicit error code in the ticket, else it
            becomes needs_more_info. Unsupported quotes also force needs_more_info.

Final retryability always comes from dataset.LABELS[class], never from model text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import openai

from casebook import packet as pk
from casebook import rules
from casebook.redact import redact
from casebook.triage.dataset import LABELS, Ticket

TEXT_SIGNALS: list[tuple[str, str]] = [   # documented phrases (case-insensitive), checked after codes
    (r"you have reached your specified (workspace )?api usage limits", "billing.self_set_spend_limit"),
    (r"cors requests are not allowed", "auth.cors_blocked"),
    (r"country, region, or territory not supported", "auth.region"),
    (r"invalid `?service_tier`?", "config.service_tier"),
    (r"resource unavailable", "capacity.flex"),
    (r"\b413\b", "request.too_large"),
    (r"apitimeouterror|client times out|never get a response", "client.timeout"),
    (r"event: error.{0,40}overloaded_error|overloaded_error.{0,40}(mid-?stream|halfway)", "provider.capacity"),
    (r"\b402\b|billing_error", "billing.payment"),
    (r"incorrect api key provided|invalid_api_key|invalid x-api-key", "auth.invalid"),
    (r"504 timeout_error|timeout_error", "provider.timeout"),
    (r"ramp_limit_exceeded", "limits.ramp"),
]
CONFIDENT = {"code", "message-prefix", "header-heuristic", "message-text"}
DESCRIPTIONS = {
    "limits.rate": "429 rate limit (requests/tokens per minute); has retry-after or rate_limit_exceeded",
    "limits.ramp": "429 ramp: traffic grew too fast (slow_down / ramp_limit_exceeded)",
    "limits.workspace": "429 where one workspace's limit is the binding one",
    "capacity.flex": "flex service tier 'Resource Unavailable'",
    "provider.capacity": "provider overloaded (503 server_is_overloaded, 529 / overloaded_error)",
    "provider.timeout": "504 / timeout_error from the provider",
    "client.timeout": "client gave up before any response (APITimeoutError)",
    "billing.credits_exhausted": "429 credit_balance_exhausted / insufficient_quota",
    "billing.spend_limit": "429 organization_ or project_spend_limit_exceeded",
    "billing.usage_limit": "429 organization_usage_limit_exceeded",
    "billing.tier_spend_cap": "429 enforced_spend_limit_reached (monthly tier cap)",
    "billing.self_set_spend_limit": "400 'You have reached your specified ... usage limits'",
    "billing.payment": "402 billing_error",
    "auth.invalid": "401 bad, revoked or wrong key",
    "auth.region": "403 country, region or territory not supported",
    "auth.cors_blocked": "401 CORS requests not allowed for this organization",
    "config.service_tier": "400 invalid service_tier",
    "request.too_large": "413 request too large",
    "needs_more_info": "the ticket lacks the error code/message needed to tell these apart",
}


@dataclass
class Result:
    ticket: str
    mode: str
    label: str
    retry: str
    basis: str
    evidence: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    prompt_had_secret: bool = False
    output_had_secret: bool = False
    raw: str | None = None


SPECIFIC_FIRST = 4   # the first TEXT_SIGNALS entries are documented messages more specific than any type/status


def triage_rules(t: Ticket) -> Result:
    clean, _ = redact(t.text)
    for pat, cls in TEXT_SIGNALS[:SPECIFIC_FIRST]:
        if re.search(pat, clean, re.I):
            return Result(t.id, "rules", cls, LABELS[cls], f"message-text /{pat}/")
    p = pk.from_text(t.text, provider=t.provider)
    v = rules.classify(p)
    label, basis = v.cls, v.confidence
    if v.confidence not in CONFIDENT or label not in LABELS:
        label, basis = "needs_more_info", f"rules: {v.confidence} ({v.basis})"
        for pat, cls in TEXT_SIGNALS:
            if re.search(pat, clean, re.I):
                label, basis = cls, f"message-text /{pat}/"
                break
    return Result(t.id, "rules", label, LABELS[label], basis)


def _prompt(text: str, shots: list[Ticket]) -> list[dict[str, str]]:
    labels = "\n".join(f"- {k}: {v}" for k, v in DESCRIPTIONS.items())
    sys_ = ("You triage API support tickets. Pick exactly one class from the list. Choose needs_more_info unless the "
            "ticket contains the specific error code or message that identifies the class — a bare HTTP status is not "
            "enough. Quote the exact words from the ticket that justify your choice. Reply with JSON only: "
            '{"class": "<class>", "evidence": ["<exact quote>", ...]}\n\nClasses:\n' + labels)
    msgs = [{"role": "system", "content": sys_}]
    for s in shots:
        q = redact(s.text)[0]
        msgs += [{"role": "user", "content": q},
                 {"role": "assistant", "content": json.dumps({"class": s.gold, "evidence": [q[:60]]})}]
    msgs.append({"role": "user", "content": text})
    return msgs


def triage_llm(t: Ticket, client: openai.OpenAI, model: str, shots: list[Ticket]) -> Result:
    clean, _ = redact(t.text)
    msgs = _prompt(clean, shots)
    prompt_secret = any("PLANTED" in m["content"] for m in msgs)
    try:
        r = client.chat.completions.create(model=model, messages=msgs, response_format={"type": "json_object"},
                                           **_generation_params(model))
        raw = r.choices[0].message.content or "{}"
        data = json.loads(raw)
    except (openai.APIError, ValueError) as e:
        # Counted separately in the eval (llm_errors) so a failing model never looks like a cautious one.
        return Result(t.id, "llm", "needs_more_info", "C", f"llm error: {type(e).__name__}: {str(e)[:120]}",
                      prompt_had_secret=prompt_secret)
    label = data.get("class") if data.get("class") in LABELS else "needs_more_info"
    ev = [str(x) for x in data.get("evidence") or [] if str(x).strip()]
    unsupported = [q for q in ev if q.lower() not in clean.lower()]
    return Result(t.id, "llm", label, LABELS[label], "llm", ev, unsupported, prompt_secret, "PLANTED" in raw, raw[:300])


def triage_combined(t: Ticket, client: openai.OpenAI, model: str, shots: list[Ticket]) -> Result:
    r = triage_rules(t)
    if r.label != "needs_more_info":
        r.mode = "combined"
        return r
    llm = triage_llm(t, client, model, shots)
    if llm.basis.startswith("llm error"):
        return Result(t.id, "combined", "needs_more_info", "C", llm.basis, prompt_had_secret=llm.prompt_had_secret)
    label, basis = llm.label, "llm (guarded)"
    has_code = bool(pk.from_text(t.text, provider=t.provider)["mentioned"]["codes"])
    if LABELS[label] == "Y" and not has_code:
        label, basis = "needs_more_info", f"guard: llm proposed retryable {llm.label} without an error code"
    if llm.unsupported:
        label, basis = "needs_more_info", "guard: llm quoted text that is not in the ticket"
    return Result(t.id, "combined", label, LABELS[label], basis, llm.evidence, llm.unsupported,
                  llm.prompt_had_secret, llm.output_had_secret, llm.raw)


def _generation_params(model: str) -> dict[str, Any]:
    """Local small models: deterministic and capped (an uncapped JSON-mode run once hung for an hour).
    OpenAI reasoning models take max_completion_tokens (reasoning tokens count against it) and
    reasoning_effort instead of temperature/max_tokens."""
    if model.startswith(("gpt-5", "gpt-6", "o3", "o4")):
        return {"max_completion_tokens": 4000, "reasoning_effort": "low"}
    return {"temperature": 0, "max_tokens": 200}


def ollama_client() -> openai.OpenAI:
    return openai.OpenAI(api_key="ollama", base_url="http://127.0.0.1:11434/v1", max_retries=1, timeout=120)


_ = Any
