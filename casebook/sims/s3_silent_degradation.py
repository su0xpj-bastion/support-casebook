"""Sim 3 — silent quality degradation on a sticky slice of traffic.

Template: Anthropic's 2025-09-17 postmortem. A routing bug sent a sticky subset of requests to a
misconfigured pool (peak 16% of Sonnet 4 requests on first-party; Bedrock 0.18%; Vertex <0.0004%);
another bug produced odd characters (e.g. Thai) in English answers. Everything returned HTTP 200, and
evals/canaries missed it.

Two detectors over 24 simulated hours per platform:
  canary       every hour, 20 fresh sessions x 1 request; alert if >= 2 bad answers in the hour
  per_session  probe a 10% sample of real customer sessions; alert when one sampled session has
               >= 3 bad answers (sticky routing makes every turn of an affected session bad)

Both detectors here recognise the corruption perfectly; the real degradation was subtler, so these
detection rates are upper bounds. The point is the gap between the two at low rates, not the values.

The routing function is the same one the mock uses (SIM-3 pack), and a live spot check pushes real
Anthropic SDK calls through the mock to confirm every degraded answer arrives as a normal 200.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any

from casebook import packs

CORRUPT = "Claim 4411: water damage สวัสดี ครับ ceiling kitchen ข้อมูล."


def degraded(session_id: str, rate: float) -> bool:
    """Sticky: the same session always lands on the same side."""
    h = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < rate


def is_bad(text: str) -> bool:
    return any("฀" <= ch <= "๿" or "一" <= ch <= "鿿" for ch in text)


def simulate(rate: float, seed: int, hours: int = 24, sessions_per_hour: int = 300, turns: int = 6,
             probe_fraction: float = 0.10) -> dict[str, Any]:
    rnd = random.Random(seed)
    canary_at = per_session_at = None
    affected_users = 0
    for h in range(hours):
        bad = sum(degraded(f"canary-{seed}-{h}-{i}-{rnd.random()}", rate) for i in range(20))
        if canary_at is None and bad >= 2:
            canary_at = h
        for s in range(sessions_per_hour):
            sid = f"user-{seed}-{h}-{s}-{rnd.random()}"
            if degraded(sid, rate):
                affected_users += 1
                probed = rnd.random() < probe_fraction
                if probed and per_session_at is None and turns >= 3:
                    per_session_at = h
    return {"canary_detect_hour": canary_at, "per_session_detect_hour": per_session_at, "affected_sessions": affected_users}


def live_spot_check(n: int = 120) -> dict[str, Any]:
    import anthropic

    from casebook.mockserver import MockServer
    from casebook.repro import make_client, request_kwargs

    out: dict[str, Any] = {}
    with MockServer() as srv:
        for platform in ("first_party", "bedrock"):
            statuses, bad = [], 0
            for i in range(n):
                c = make_client("anthropic", srv.url, pack_id="SIM-3", max_retries=0,
                                headers={"X-Session-Id": f"live-{platform}-{i}", "X-Platform": platform})
                raw = c.messages.with_raw_response.create(**request_kwargs("anthropic"))
                statuses.append(raw.status_code)
                text = raw.parse().content[0].text
                bad += is_bad(text)
                assert is_bad(text) == degraded(f"live-{platform}-{i}", packs.get("SIM-3").expected["rates"][platform])
            out[platform] = {"requests": n, "bad_answers": bad, "non_200": sum(1 for s in statuses if s != 200)}
        _ = anthropic
    return out


def run(seeds: int = 20) -> dict[str, Any]:
    rates = packs.get("SIM-3").expected["rates"]
    out: dict[str, Any] = {}
    for platform, rate in rates.items():
        runs = [simulate(rate, seed) for seed in range(seeds)]
        c = [r["canary_detect_hour"] for r in runs if r["canary_detect_hour"] is not None]
        p = [r["per_session_detect_hour"] for r in runs if r["per_session_detect_hour"] is not None]
        out[platform] = {"rate": rate, "canary_detection_rate": len(c) / seeds, "per_session_detection_rate": len(p) / seeds,
                         "canary_median_hour": sorted(c)[len(c) // 2] if c else None,
                         "per_session_median_hour": sorted(p)[len(p) // 2] if p else None,
                         "affected_sessions_per_day_median": sorted(r["affected_sessions"] for r in runs)[seeds // 2]}
    out["live_spot_check"] = live_spot_check()
    fp, br = out["first_party"], out["bedrock"]
    out["finding"] = {"cls": "sim.silent_degradation", "detail": (
        f"16% sticky slice: canary caught it in {fp['canary_detection_rate']:.0%} of runs (median hour {fp['canary_median_hour']}), "
        f"per-session probes {fp['per_session_detection_rate']:.0%} (hour {fp['per_session_median_hour']}); 0.18% slice: canary "
        f"{br['canary_detection_rate']:.0%}, per-session {br['per_session_detection_rate']:.0%} "
        f"(~{br['affected_sessions_per_day_median']} affected sessions/day); every degraded answer was HTTP 200 "
        f"(live: {out['live_spot_check']['first_party']['bad_answers']}/{out['live_spot_check']['first_party']['requests']} bad, "
        f"{out['live_spot_check']['first_party']['non_200']} non-200)")}
    return out
