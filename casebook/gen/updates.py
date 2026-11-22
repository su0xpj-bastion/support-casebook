"""Generate customer updates, internal escalation briefs and RCAs from captured evidence — never
from free text alone — and lint every customer update before a human sends it.

Required customer-update sections: confirmed facts, customer impact, current
mitigation, owner, next update, explicit unknowns.
RCA sections follow the five-part structure of OpenAI's public incident write-ups:
Summary, Impact, Root cause, Mitigation and recovery, Prevention and follow-up.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from casebook.redact import redact

UPDATE_SECTIONS = ("Confirmed facts", "Customer impact", "Current mitigation", "Owner", "Next update", "Explicit unknowns")
RCA_SECTIONS = ("Summary", "Impact", "Root cause", "Mitigation and recovery", "Prevention and follow-up")


def _fmt(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def customer_update(*, title: str, severity: str, facts: list[str], impact: str, mitigation: str, owner: str,
                    next_update: dt.datetime | None, unknowns: list[str], retryable: str | None = None,
                    now: dt.datetime, resolved: bool = False) -> str:
    """retryable: the evidence-based verdict ('Y'/'N'/'C') — drives whether retry advice is allowed."""
    lines = [f"### {title} — {severity} — update at {_fmt(now)}", ""]
    lines += ["**Confirmed facts**", *[f"- {f}" for f in facts], ""]
    lines += ["**Customer impact**", impact, ""]
    advice = mitigation
    if retryable == "N":
        advice += " Retrying will not help until this is resolved; please pause automatic retries for this error."
    elif retryable == "Y":
        advice += " Requests are safe to retry with exponential backoff and jitter."
    lines += ["**Current mitigation**", advice, ""]
    lines += ["**Owner**", owner, ""]
    nxt = "Resolved — final update; RCA to follow" if resolved else (_fmt(next_update) if next_update else "when there is new information")
    lines += ["**Next update**", nxt, ""]
    lines += ["**Explicit unknowns**", *([f"- {u}" for u in unknowns] or ["- none at this time"]), ""]
    return "\n".join(lines)


def escalation_brief(*, title: str, packet: dict[str, Any], verdict: dict[str, Any], repro: str,
                     window: tuple[dt.datetime, dt.datetime], blast_radius: str, ask: str) -> str:
    rid = packet.get("request_ids") or {}
    err = packet.get("error") or {}
    lines = [
        f"## Escalation: {title}", "",
        f"- **Verdict:** {verdict['cls']} (retryable={verdict['retryable']}, owner={verdict['owner']}, basis: {verdict['basis']})",
        f"- **Window:** {_fmt(window[0])} → {_fmt(window[1])}",
        f"- **Request IDs:** provider={rid.get('provider')} client={rid.get('client')} cloud={rid.get('cloud')}",
        f"- **Org / project-workspace:** {packet.get('org_id')} / {packet.get('project_or_workspace')}",
        f"- **Error:** status={packet['http']['status']} type={err.get('type')} code={err.get('code') or err.get('details_error_code')}",
        f"- **SDK:** {packet.get('sdk')}",
        f"- **Blast radius:** {blast_radius}",
        f"- **Repro:** `{repro}`",
        f"- **Ask:** {ask}", "",
    ]
    return "\n".join(lines)


def rca(*, title: str, summary: str, impact: str, root_cause: str, evidence: list[str], mitigation: list[str],
        prevention: list[str]) -> str:
    lines = [f"# RCA: {title}", "", "## Summary", summary, "", "## Impact", impact, "", "## Root cause", root_cause, "",
             "Evidence:", *[f"- {e}" for e in evidence], "", "## Mitigation and recovery", *[f"- {m}" for m in mitigation], "",
             "## Prevention and follow-up", *[f"- {p}" for p in prevention], ""]
    return "\n".join(lines)


ROOT_CAUSE_CLAIM = re.compile(r"\b(root cause (is|was)|caused by|due to)\b", re.I)
RETRY_ADVICE = re.compile(r"\b(please retry|retry (the|your) request|safe to retry|try again)\b", re.I)


def lint_update(text: str, *, retryable: str | None = None, severity: str | None = None) -> list[str]:
    """Reject updates that would mislead or leak. Empty list = pass."""
    problems = [f"missing section: {s}" for s in UPDATE_SECTIONS if f"**{s}**" not in text]
    _, secrets = redact(text)
    if secrets:
        problems.append(f"contains secrets: {dict(secrets)}")
    if retryable == "N" and RETRY_ADVICE.search(text):
        problems.append("tells the customer to retry a non-retryable failure")
    facts = text.split("**Customer impact**")[0]
    if ROOT_CAUSE_CLAIM.search(text) and "evidence:" not in text.lower() and "request id" not in facts.lower():
        problems.append("states a root cause without citing evidence (request IDs / evidence line)")
    if severity == "P1" and "**Next update**" in text:
        nxt = text.split("**Next update**")[1].split("**")[0]
        if "UTC" not in nxt and "Resolved — final update" not in nxt:
            problems.append("P1 update without a concrete next-update time")
    return problems
