"""Drive ts/repro.mjs (official TypeScript SDKs on Node) against the mock and count requests."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from casebook import packs
from casebook.mockserver import MockServer

TS_DIR = Path(__file__).resolve().parent.parent / "ts"


def available() -> bool:
    return shutil.which("node") is not None and (TS_DIR / "node_modules" / "openai").exists() \
        and (TS_DIR / "node_modules" / "@anthropic-ai" / "sdk").exists()


def run(pack_id: str, srv: MockServer, *, timeout_s: float = 120) -> dict[str, Any]:
    pack = packs.get(pack_id)
    meta = pack.meta
    srv.state.reset()
    args = [
        "node", str(TS_DIR / "repro.mjs"), pack.provider, srv.url, pack_id,
        json.dumps(meta.get("request", {})),
        str(meta.get("client", {}).get("timeout", "")),
        (meta.get("request_headers") or {}).get("X-Client-Request-Id", ""),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s, cwd=TS_DIR)
    if proc.returncode != 0:
        raise RuntimeError(f"node failed for {pack_id}: {proc.stderr[-500:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    out["requests_sent"] = srv.state.hits.get(pack_id, 0)
    out["request_ids_seen"] = [r["request_id"] for r in srv.state.requests if r["key"] == pack_id]
    return out
