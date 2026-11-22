"""Run the three sims and write their artifacts to cases/sims/<sim>/: report.json (measured numbers),
customer updates, an internal escalation brief and an RCA — all generated from the run, all linted."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from casebook.gen.updates import customer_update, escalation_brief, lint_update, rca
from casebook.sims import s1_outage, s2_retry_storm, s3_silent_degradation

OUT = Path(__file__).resolve().parents[2] / "cases" / "sims"


def _write(d: Path, name: str, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def sim1() -> dict[str, Any]:
    r = s1_outage.run()
    d = OUT / "SIM-1-provider-outage"
    report = {k: v for k, v in r.items() if k not in ("timeline", "first_update", "lint")}
    _write(d, "report.json", json.dumps(report, indent=2) + "\n")
    _write(d, "timeline.jsonl", "\n".join(json.dumps(x) for x in r["timeline"]) + "\n")
    _write(d, "customer-update-1.md", r["first_update"] or "")
    start = dt.datetime(2024, 12, 11, 23, 43, tzinfo=dt.timezone.utc)
    packet = {"request_ids": {"provider": None, "client": "claims-svc-7f3a-0001", "cloud": None}, "org_id": "org-casebook0001",
              "project_or_workspace": None, "http": {"status": None}, "error": {"type": "connection_error"},
              "sdk": {"name": "openai-python", "version": "3.51.0"}}
    _write(d, "internal-escalation.md", escalation_brief(
        title="Connection failures across all keys and endpoints", packet=packet,
        verdict={"cls": "sim.provider_outage", "retryable": "C", "owner": "provider", "basis": "connection errors on >=2 endpoints from >=60% of clients"},
        repro="python -m casebook repro SIM-1", window=(start, start + dt.timedelta(hours=1)),
        blast_radius=f"{r['timeline'][-1].get('of', 10)} of 10 monitored clients; chat completions and model listing",
        ask="Confirm incident and ETA; is login (dashboard) on the same dependency?"))
    _write(d, "rca.md", rca(
        title="SIM-1 provider outage (template: OpenAI 2024-12-11)",
        summary="A platform dependency failed and every API call and dashboard sign-in failed at the connection level for one hour.",
        impact="All keys and endpoints; no requests completed during the window.",
        root_cause="Platform-side (in the template incident: a telemetry deployment overloaded the Kubernetes control plane, breaking DNS-based service discovery).",
        evidence=[f"provider-side verdict {r['minutes_to_provider_side']} min after impact from client packets", "no 4xx except one revoked key (control)",
                  "timeline.jsonl in this folder"],
        mitigation=["customer updates every 30 min with explicit unknowns", f"recovery confirmed from client signals {r['minutes_to_see_recovery']} min after it happened"],
        prevention=["alert on cross-client connection-error ratio, not per-key error counts", "pre-written P1 update template"]))
    return report


def sim2() -> dict[str, Any]:
    r = s2_retry_storm.run()
    d = OUT / "SIM-2-retry-storm"
    _write(d, "report.json", json.dumps(r, indent=2) + "\n")
    now = dt.datetime(2026, 4, 20, 16, 0, tzinfo=dt.timezone.utc)
    n, b = r["naive"], r["budgeted"]
    text = customer_update(
        title="Your retry settings are extending the outage", severity="P1",
        facts=[f"Measured on the same incident: unbounded immediate retries sent {n['amplification']}x the requests and did not recover "
               "after capacity returned", f"A jittered backoff with a retry budget sent {b['amplification']}x and recovered first"],
        impact="While the platform is degraded, your client fleet's retries keep the endpoint saturated for you and others.",
        mitigation="Cap retries (backoff with jitter, at most ~10% extra traffic), or rely on the SDK defaults.",
        owner="Support (casebook)", next_update=now + dt.timedelta(minutes=30), unknowns=["platform ETA"], retryable="C", now=now)
    _write(d, "customer-guidance.md", text)
    return {**r, "guidance_lint": lint_update(text, severity="P1")}


def sim3() -> dict[str, Any]:
    r = s3_silent_degradation.run()
    d = OUT / "SIM-3-silent-degradation"
    _write(d, "report.json", json.dumps(r, indent=2) + "\n")
    now = dt.datetime(2025, 9, 1, 15, 0, tzinfo=dt.timezone.utc)
    text = customer_update(
        title="Reports of lower-quality answers (no errors)", severity="P2",
        facts=["Affected answers return HTTP 200; no error codes are involved",
               "The same conversations are affected repeatedly while others are fine (consistent with sticky routing)"],
        impact="Some sessions receive degraded or garbled answers; most sessions are unaffected.",
        mitigation="We have escalated with request IDs grouped by platform and date. Please send 3–5 request IDs from affected sessions and the platform you call through.",
        owner="Support (casebook)", next_update=now + dt.timedelta(hours=1),
        unknowns=["whether the cause is model, routing or client-side", "which platforms are affected"], retryable=None, now=now)
    _write(d, "customer-update-1.md", text)
    return {**{k: v for k, v in r.items() if k != "live_spot_check"}, "update_lint": lint_update(text, severity="P2")}


def run_all() -> dict[str, Any]:
    return {"SIM-1": sim1(), "SIM-2": sim2(), "SIM-3": sim3()}


if __name__ == "__main__":
    print(json.dumps({k: v.get("finding", {}).get("detail") for k, v in run_all().items()}, indent=2))
