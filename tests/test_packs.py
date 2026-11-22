"""Every error pack, through the real Python SDKs, both ways (SDK defaults vs evidence-aware policy)."""
from __future__ import annotations

import jsonschema
import pytest

from casebook import packet as pk
from casebook import packs, policy, repro

ALL = packs.load_all()
PACKS = sorted(ALL)
ERROR_PACKS = sorted(p for p, v in ALL.items() if v.meta.get("kind", "error") == "error")
SCHEMA = pk.load_schema()
REQUIRED_META = {"id", "kind", "title", "provider", "surface", "endpoint", "tier", "retry", "verdict",
                 "looks_like", "evidence_required", "docs", "expect_requests"}
TAGS = {"doc-verbatim", "doc-partial", "live-captured", "synthesized"}


@pytest.fixture(scope="module")
def runs(mock) -> dict[str, dict]:
    out = {}
    for pid in ERROR_PACKS:
        sleeps: list[float] = []
        out[pid] = {
            "sdk": repro.run(pid, mock, "sdk-default"),
            "policy": repro.run(pid, mock, "policy", sleep=sleeps.append),
            "sleeps": sleeps,
        }
    return out


def test_core_error_pack_set_present() -> None:
    billing_and_capacity = {"OA-04", "OA-05", "OA-06", "OA-10", "AN-01", "AN-02", "AN-03", "AN-08"}
    limits_auth_anomalies = {f"OA-{n:02d}" for n in (1, 2, 7, 8, 11, 12, 13, 14, 15)} | {f"AN-{n:02d}" for n in range(4, 17)}
    assert billing_and_capacity | limits_auth_anomalies <= set(PACKS)


@pytest.mark.parametrize("pid", PACKS)
def test_pack_metadata_complete(pid: str) -> None:
    p = ALL[pid]
    assert REQUIRED_META <= set(p.meta), REQUIRED_META - set(p.meta)
    if p.meta["kind"] == "error":
        assert p.meta["retry"] == p.meta["verdict"]["retryable"]
    assert all(u.startswith("https://") for u in p.meta["docs"])
    assert (p.path / "fix.md").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("pid", PACKS)
def test_pack_provenance_tagged_per_field(pid: str) -> None:
    prov = ALL[pid].expected["provenance"]
    assert prov and set(prov.values()) <= TAGS


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_rules_verdict_matches_pack(runs, pid: str) -> None:
    expected = ALL[pid].meta["verdict"]
    got = runs[pid]["sdk"]["verdict"]
    assert {k: got[k] for k in expected} == expected


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_packet_valid(runs, pid: str) -> None:
    p = runs[pid]["sdk"]["packet"]
    jsonschema.validate(p, SCHEMA)
    assert p["http"]["status"] == ALL[pid].expected.get("status") or p["http"]["status"] is None
    assert not [w for w in p["warnings"] if "!=" in w]


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_request_id_present_unless_none_can_exist(runs, pid: str) -> None:
    p = runs[pid]["sdk"]["packet"]
    no_rid_expected = ALL[pid].expected.get("omit_base_headers") or p["error"]["type"] == "client_timeout"
    if no_rid_expected:
        assert p["request_ids"]["provider"] is None
        assert any("request id" in w for w in p["warnings"])
    else:
        assert p["request_ids"]["provider"].startswith("req_")


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_sdk_default_exception_class(runs, pid: str) -> None:
    want = ALL[pid].meta.get("sdk_exception", {}).get("python")
    if want:
        assert runs[pid]["sdk"]["exception"] == want


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_request_counts_python_sdk_default_vs_policy(runs, pid: str) -> None:
    """SDK retry counts observed and asserted against each pack's expectation."""
    exp = ALL[pid].meta["expect_requests"]
    assert runs[pid]["sdk"]["requests_sent"] == exp["python_sdk_default"]
    if exp["policy"] is not None:
        assert runs[pid]["policy"]["requests_sent"] == exp["policy"]


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_policy_backoff_honours_retry_after(runs, pid: str) -> None:
    if runs[pid]["policy"]["requests_sent"] is None:
        pytest.skip("stream pack")
    ra = runs[pid]["policy"]["packet"]["http"]["retry_after"]
    sleeps = runs[pid]["sleeps"]
    if ALL[pid].meta["retry"] != "Y":
        assert sleeps == []
    else:
        assert len(sleeps) == policy.MAX_ATTEMPTS - 1
        assert all(s >= (ra or 0) and s >= 2 ** i for i, s in enumerate(sleeps))


@pytest.mark.parametrize("pid", ERROR_PACKS)
def test_policy_trace_records_every_failed_attempt(runs, pid: str) -> None:
    """Added after a mutation in policy.call_openai (dropping the caller's trace) passed every test."""
    pol = runs[pid]["policy"]
    if pol["requests_sent"] is None:
        pytest.skip("stream pack")
    trace = pol["trace"]
    if pol["exception"] and pol["packet"]["http"]["status"] is not None:   # timeouts/connection errors aren't traced
        assert len(trace) == pol["requests_sent"], trace
        assert [t["attempt"] for t in trace] == list(range(1, len(trace) + 1))
        assert trace[-1]["sleep"] is None and trace[-1]["verdict"]["cls"] == pol["verdict"]["cls"]


def test_an01_has_no_retry_after_and_an03_does(runs) -> None:
    assert runs["AN-01"]["sdk"]["packet"]["http"]["retry_after"] is None
    assert runs["AN-03"]["sdk"]["packet"]["http"]["retry_after"] == 1.0


def test_oa10_is_not_a_rate_limit_error(runs) -> None:
    assert runs["OA-10"]["sdk"]["exception"] == "openai.InternalServerError"


def test_message_matched_rules_are_flagged_fragile(runs) -> None:
    for pid in ("AN-02", "AN-12", "OA-08", "OA-11"):
        assert runs[pid]["sdk"]["verdict"]["confidence"] == "message-prefix", pid


def test_an05_workspace_limit_is_a_header_heuristic(runs) -> None:
    assert runs["AN-05"]["sdk"]["verdict"]["confidence"] == "header-heuristic"


def test_an09_midstream_error_keeps_partial_text_and_is_not_overloaded_class(runs) -> None:
    r = runs["AN-09"]["sdk"]
    assert r["partial_text"] == "Claim #4411: water damage, "
    assert r["packet"]["stream_error"] is True and r["packet"]["http"]["status"] == 200
    # Code that catches anthropic.OverloadedError misses this: it arrives as a generic APIStatusError.
    assert r["exception"] == "anthropic.APIStatusError"


def test_an11_sdk_retries_mint_three_request_ids_for_one_logical_call(runs) -> None:
    ids = runs["AN-11"]["sdk"]["request_ids_seen"]
    assert len(ids) == 3 and len(set(ids)) == 3


def test_an13_and_an14_are_indistinguishable_at_the_api(runs) -> None:
    a, b = runs["AN-13"]["sdk"]["packet"]["error"], runs["AN-14"]["sdk"]["packet"]["error"]
    assert (a["type"], a["message"]) == (b["type"], b["message"])


def test_an16_edge_413_has_no_envelope(runs) -> None:
    p = runs["AN-16"]["sdk"]["packet"]
    assert p["error"]["type"] is None
    assert any("before the API" in w for w in p["warnings"])


def test_oa14_timeout_packet_carries_client_request_id(runs) -> None:
    p = runs["OA-14"]["sdk"]["packet"]
    assert p["request_ids"] == {"provider": None, "client": "claims-svc-7f3a-0001", "cloud": None}
    assert runs["OA-14"]["sdk"]["exception"] == "openai.APITimeoutError"


def test_oa12_flex_uses_request_side_evidence(runs) -> None:
    p = runs["OA-12"]["sdk"]["packet"]
    assert p["request_meta"]["service_tier"] == "flex"
