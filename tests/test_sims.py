"""Executable incident sims. Assert shape and ordering, not exact timings."""
from __future__ import annotations

import pytest

from casebook.sims import s1_outage, s2_retry_storm, s3_silent_degradation


@pytest.fixture(scope="module")
def s2() -> dict:
    return s2_retry_storm.run()


def test_sim1_classifies_provider_side_and_updates_lint_clean() -> None:
    r = s1_outage.run()
    assert r["minutes_to_provider_side"] is not None and r["minutes_to_provider_side"] <= 10
    assert r["verdicts_before_outage"] == ["customer-side"]          # the revoked-key control never looks like an outage
    assert r["control_client_4xx"] > 0
    assert r["updates"] >= 2 and r["lint_problems"] == 0
    assert r["minutes_to_see_recovery"] is not None


def test_sim2_amplification_ordering(s2) -> None:
    n, d, b = s2["naive"], s2["sdk_default"], s2["budgeted"]
    assert n["amplification"] > 5 > d["amplification"] > b["amplification"] >= 1.0
    assert n["recovery_s_after_capacity_restored"] is None            # retry storm keeps it saturated
    assert b["recovery_s_after_capacity_restored"] is not None
    assert d["success_rate"] > b["success_rate"]                       # the trade-off, reported not hidden


def test_sim3_low_rate_slice_beats_canaries_not_per_session() -> None:
    r = s3_silent_degradation.run(seeds=10)
    assert r["first_party"]["canary_detection_rate"] == 1.0
    assert r["bedrock"]["canary_detection_rate"] <= 0.2 and r["bedrock"]["per_session_detection_rate"] >= 0.6
    live = r["live_spot_check"]["first_party"]
    assert live["bad_answers"] > 0 and live["non_200"] == 0


def test_sim3_routing_is_sticky() -> None:
    assert all(s3_silent_degradation.degraded("same-session", 0.5) == s3_silent_degradation.degraded("same-session", 0.5)
               for _ in range(5))
