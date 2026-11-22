"""Live captures are checked in as evidence. These tests never touch the network."""
from __future__ import annotations

import json

import pytest

from casebook import live, packs
from casebook.redact import redact

CAPTURES = sorted(p for pid in live.FREE for p in (packs.get(pid).path / "live").glob("*.json"))


def test_every_free_pack_has_a_capture() -> None:
    have = {p.parent.parent.name.split("-")[0] + "-" + p.parent.parent.name.split("-")[1] for p in CAPTURES}
    assert set(live.FREE) <= have


@pytest.mark.parametrize("path", CAPTURES, ids=lambda p: p.parent.parent.name)
def test_capture_is_well_formed_and_leaks_nothing(path) -> None:
    text = path.read_text(encoding="utf-8")
    cap = json.loads(text)
    assert cap["provenance"] == "live-captured" and cap["status"]
    assert live.FAKE_OPENAI_KEY not in text and live.FAKE_ANTHROPIC_KEY not in text
    _, hits = redact(text)
    assert not hits, hits
    assert all({"field", "expected", "live", "match"} <= set(r) for r in cap["diff"])


def test_known_live_results() -> None:
    def latest(pid):
        return json.loads(sorted((packs.get(pid).path / "live").glob("*.json"))[-1].read_text())
    an13, oa07, an16 = latest("AN-13"), latest("OA-07"), latest("AN-16")
    assert an13["x_should_retry"] == "false" and all(r["match"] for r in an13["diff"])
    assert oa07["x_should_retry"] is None
    assert {r["field"]: r["match"] for r in oa07["diff"]}["error.code"] is True
    assert an16["status"] == 401  # auth evaluated before (or instead of) the 413 size rejection
