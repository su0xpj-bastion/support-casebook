"""Load fault packs from faults/<provider>/<ID>-<slug>/{pack.yaml, expected.json, fix.md}."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent / "faults"
PROVENANCE_ORDER = ["live-captured", "doc-verbatim", "doc-partial", "synthesized"]


@dataclass(frozen=True)
class Pack:
    id: str
    path: Path
    meta: dict[str, Any]
    expected: dict[str, Any]

    @property
    def provider(self) -> str:
        return self.meta["provider"]

    @property
    def provenance(self) -> str:
        """Weakest provenance across the fields of the expected response."""
        tags = set(self.expected.get("provenance", {}).values()) or {"synthesized"}
        return max(tags, key=PROVENANCE_ORDER.index)


@lru_cache(maxsize=1)
def load_all() -> dict[str, Pack]:
    packs: dict[str, Pack] = {}
    for meta_path in sorted(ROOT.glob("*/*/pack.yaml")):
        with meta_path.open("r", encoding="utf-8") as fh:
            meta = yaml.safe_load(fh)
        with (meta_path.parent / "expected.json").open("r", encoding="utf-8") as fh:
            expected = json.load(fh)
        packs[meta["id"]] = Pack(meta["id"], meta_path.parent, meta, expected)
    return packs


def get(pack_id: str) -> Pack:
    return load_all()[pack_id]
