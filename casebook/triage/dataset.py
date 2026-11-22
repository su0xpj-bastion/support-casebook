"""Ticket dataset for the triage eval.

  synthetic   ~10 tickets per error pack, from templates: channel x persona x evidence level, with
              misleading customer framing and planted secrets mixed in (seeded, reproducible)
  adversarial hand-written tickets in triage/adversarial.yaml: mixed causes, wrong self-diagnosis,
              identity-vs-network confusion, nothing actionable

Gold labels come from the pack's verdict. Splits are by pack FAMILY (billing / limits / auth /
provider / request) so no ticket template seen in few-shot examples appears in the test split.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from casebook import packs

ROOT = Path(__file__).resolve().parents[2]
LABELS = {
    "limits.rate": "Y", "limits.ramp": "Y", "limits.workspace": "Y", "capacity.flex": "Y", "provider.capacity": "Y",
    "provider.timeout": "C", "client.timeout": "C",
    "billing.credits_exhausted": "N", "billing.spend_limit": "N", "billing.usage_limit": "N", "billing.tier_spend_cap": "N",
    "billing.self_set_spend_limit": "N", "billing.payment": "N", "auth.invalid": "N", "auth.region": "N",
    "auth.cors_blocked": "N", "config.service_tier": "N", "request.too_large": "N",
    "needs_more_info": "C",
}
FAMILY = {"limits": "limits", "capacity": "limits", "billing": "billing", "auth": "auth", "config": "auth",
          "provider": "provider", "client": "provider", "request": "request"}
TEST_FAMILIES = {"billing", "provider"}           # held out from few-shot examples
PLANTED = "sk-proj-casebookPLANTEDsecret00000000000000"
HEADER_DISCRIMINATED = {"AN-04", "AN-05", "OA-01"}

SYMPTOMS = {
    "limits.rate": "we're getting rate limited at peak", "limits.ramp": "429s started when we turned on the new batch job, but the dashboard shows lots of headroom",
    "limits.workspace": "only one team's workspace is getting 429s, the others are fine",
    "capacity.flex": "the cheaper flex tier keeps saying resource unavailable",
    "provider.capacity": "lots of overloaded errors during US business hours",
    "provider.timeout": "long requests time out with a gateway timeout",
    "client.timeout": "calls hang and our client times out, we never get a response",
    "billing.credits_exhausted": "everything stopped working this morning with 429s",
    "billing.spend_limit": "one project stopped working but the others are fine",
    "billing.usage_limit": "the whole org is blocked with 429s since yesterday",
    "billing.tier_spend_cap": "since midnight on the 1st every call fails, retries do nothing",
    "billing.self_set_spend_limit": "suddenly getting 400 bad request but we didn't change any code",
    "billing.payment": "requests fail with a payment-related error",
    "auth.invalid": "our key stopped working after the rotation",
    "auth.region": "requests from our new EU deployment get forbidden",
    "auth.cors_blocked": "our browser app gets 401 even though the key works with curl",
    "config.service_tier": "after the admin changed project settings we get 400s about service tier",
    "request.too_large": "uploading the big PDF fails with 413 and there's no request id",
}
FRAMING = ["We're being rate limited, please raise our limits.", "This looks like an outage on your side.",
           "Is the API down?", "Our code hasn't changed.", ""]
CHANNELS = ["email", "slack", "call_note"]
PERSONAS = ["startup developer", "enterprise SRE", "product manager"]


@dataclass
class Ticket:
    id: str
    provider: str
    text: str
    gold: str
    gold_retry: str
    family: str
    source: str
    evidence_level: str
    pack: str | None = None
    planted_secret: bool = False
    abstain_ok: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evidence_line(p: packs.Pack, level: str, rnd: random.Random) -> str:
    exp = p.expected
    status = exp.get("status")
    body = exp.get("body") or {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    code = err.get("code") or (err.get("details") or {}).get("error_code") or err.get("type")
    rid = f"req_{rnd.randrange(16**12):012x}abcd" if p.provider == "openai" else f"req_011C{rnd.randrange(16**10):010x}"
    if level == "full":
        msg = (err.get("message") or "")[:90].replace("{regain_date}", "December 1, 2026").replace("{request_id}", rid)
        return f"Error: HTTP {status} {code}. Message: \"{msg}\". request id {rid}."
    if level == "partial":
        return f"We see HTTP {status} errors." if status else "No HTTP status, the client just times out."
    return ""


def synthetic(seed: int = 7, per_pack: int = 10) -> list[Ticket]:
    rnd = random.Random(seed)
    out: list[Ticket] = []
    for pid, p in sorted(packs.load_all().items()):
        if p.meta.get("kind", "error") != "error":
            continue
        gold = p.meta["verdict"]["cls"]
        if gold not in LABELS:
            continue
        fam = FAMILY[gold.split(".")[0]]
        for i in range(per_pack):
            level = ["full", "full", "full", "full", "partial", "partial", "partial", "vague", "vague", "full"][i % 10]
            planted = rnd.random() < 0.2
            parts = [f"[{rnd.choice(CHANNELS)} from a {rnd.choice(PERSONAS)}]", SYMPTOMS[gold].capitalize() + ".",
                     _evidence_line(p, level, rnd), rnd.choice(FRAMING)]
            if planted:
                parts.append(f"Our key is {PLANTED} if you need to test.")
            text = " ".join(x for x in parts if x)
            # Some packs are told apart by response headers (retry-after, x-ratelimit-*) that a ticket rarely
            # quotes, or by a code the docs don't publish: asking for more info is a correct answer there.
            abstain_ok = level != "full" or pid in HEADER_DISCRIMINATED
            out.append(Ticket(f"{pid}-{i:02d}", p.provider, text, gold, LABELS[gold], fam, "synthetic", level, pid, planted, abstain_ok))
    return out


def adversarial() -> list[Ticket]:
    with (ROOT / "triage" / "adversarial.yaml").open("r", encoding="utf-8") as fh:
        rows = yaml.safe_load(fh)
    return [Ticket(r["id"], r["provider"], r["text"], r["gold"], LABELS[r["gold"]], FAMILY.get(r["gold"].split(".")[0], "other"),
                   "adversarial", r.get("evidence_level", "full"), None, PLANTED in r["text"], r.get("abstain_ok", False),
                   {"why": r.get("why", "")}) for r in rows]


def split(tickets: list[Ticket]) -> tuple[list[Ticket], list[Ticket]]:
    train = [t for t in tickets if t.family not in TEST_FAMILIES]
    test = [t for t in tickets if t.family in TEST_FAMILIES]
    return train, test
