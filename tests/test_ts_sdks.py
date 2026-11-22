"""Same error packs through the official TypeScript SDKs (openai-node, @anthropic-ai/sdk) on Node."""
from __future__ import annotations

import pytest

from casebook import packs, ts_repro

ALL = packs.load_all()
TS_PACKS = sorted(p for p, v in ALL.items()
                  if v.meta.get("kind", "error") == "error" and "ts_sdk_default" in v.meta["expect_requests"])

pytestmark = pytest.mark.skipif(not ts_repro.available(), reason="node + ts/node_modules not installed (cd ts && npm ci)")


@pytest.fixture(scope="module")
def ts_runs(mock) -> dict[str, dict]:
    return {pid: ts_repro.run(pid, mock) for pid in TS_PACKS}


@pytest.mark.parametrize("pid", TS_PACKS)
def test_ts_request_counts(ts_runs, pid: str) -> None:
    assert ts_runs[pid]["requests_sent"] == ALL[pid].meta["expect_requests"]["ts_sdk_default"]


@pytest.mark.parametrize("pid", TS_PACKS)
def test_ts_surfaces_an_error(ts_runs, pid: str) -> None:
    assert ts_runs[pid]["exception"], ts_runs[pid]


def test_ts_anthropic_has_dedicated_spend_cap_class_but_still_retries(ts_runs) -> None:
    r = ts_runs["AN-01"]
    assert r["exception"] == "EnforcedSpendLimitReachedError" and r["requests_sent"] == 3


def test_ts_midstream_error_keeps_partial_text(ts_runs) -> None:
    assert ts_runs["AN-09"]["partial_text"] == "Claim #4411: water damage, "
    assert ts_runs["AN-09"]["requests_sent"] == 1


def test_ts_edge_413_has_no_request_id(ts_runs) -> None:
    assert ts_runs["AN-16"]["request_id"] is None and ts_runs["AN-16"]["status"] == 413
