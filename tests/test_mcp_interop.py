"""MCP interop between two real SDKs (Python mcp 2.3.0, TypeScript 1.32.2) through
misbehaving middleboxes. Slowest module (~30 s): real servers, real clients, real proxies."""
from __future__ import annotations

import importlib.util
import shutil

import pytest

from casebook.mcp_lab import scenarios as S
from casebook.mcp_lab.servers import TS_DIR

HAS_TS_MCP = shutil.which("node") is not None and (TS_DIR / "node_modules" / "@modelcontextprotocol" / "sdk").exists()
HAS_PY_MCP = importlib.util.find_spec("mcp") is not None
pytestmark = pytest.mark.skipif(not HAS_PY_MCP, reason="mcp not installed")
needs_ts = pytest.mark.skipif(not HAS_TS_MCP, reason="cd ts && npm ci")


@needs_ts
def test_ag16_revision_matrix() -> None:
    r = S.ag16()
    assert r["ts_1_32_client__py_2_3_server"]["protocol"] == "2025-11-25" and r["ts_1_32_client__py_2_3_server"]["call"]
    assert r["py_auto__ts_server"]["result"] == "claim 4411: open" and r["py_auto__ts_server"]["protocol"] == "2025-11-25"
    assert r["py_pinned_2026_07_28__ts_server"].get("error")
    assert r["py_pinned_2026_07_28__py_server"]["protocol"] == "2026-07-28"


def test_ag17_header_mismatch_and_silent_downgrade() -> None:
    r = S.ag17()
    assert "mcp-method header does not match" in r["strip_mcp_method__pinned_modern"]["error"]
    auto = r["strip_mcp_method__auto"]
    assert auto["result"] == "claim 4411: open" and auto["protocol"] == "2025-11-25"
    assert "Missing session ID" in r["gateway_rewrites_protocol_version"]["error"]


def test_ag18_buffering_only_when_header_lost() -> None:
    r = S.ag18()
    assert r["direct"]["first_progress_s"] < 0.3
    assert r["buffering_proxy_header_present"]["first_progress_s"] < 0.3
    lost = r["buffering_proxy_header_dropped_upstream"]
    assert lost["first_progress_s"] > 1.2 and abs(lost["first_progress_s"] - lost["call_s"]) < 0.2


def test_ag19_scope_vs_audience() -> None:
    r = S.ag19()
    assert r["no_token"]["status"] == 401 and r["tok-bogus"]["status"] == 401
    assert r["tok-wrong-audience"]["status"] == 401
    assert r["tok-wrong-audience"]["www_authenticate"] == r["no_token"]["www_authenticate"]
    assert r["tok-no-scope"]["status"] == 403 and 'error="insufficient_scope"' in r["tok-no-scope"]["www_authenticate"]
    assert r["tok-good"]["status"] == 200
    assert r["protected_resource_metadata"]["scopes_supported"] == ["claims:read"]


def test_ag20_dcr_vs_cimd() -> None:
    r = S.ag20()
    assert r["as_dcr__dcr_only_client"]["authorize_client_id"] == "dcr-client-0001"
    assert "/register" in r["as_dcr__dcr_only_client"]["as_requests"]
    assert r["as_cimd__dcr_only_client"]["outcome"] == "failed" and "404" in r["as_cimd__dcr_only_client"]["error"]
    assert r["as_cimd__cimd_capable_client"]["authorize_client_id"].startswith("https://")
    assert "/register" not in r["as_cimd__cimd_capable_client"]["as_requests"]
    assert r["as_cimd__cimd_capable_client"]["resource"].endswith("/mcp")  # RFC 8707 resource indicator sent


@needs_ts
def test_ag21_one_bad_tool_hides_all_tools() -> None:
    r = S.ag21()
    assert "invalid result" in r["reference_server__py_client"]["error"]
    assert "ValidationError" in r["third_party_server__py_client"]["error"]
    assert "ZodError" in r["third_party_server__ts_client"]["error"] and r["third_party_server__ts_client"]["tools"] is None
