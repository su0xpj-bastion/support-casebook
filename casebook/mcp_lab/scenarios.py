"""AG-16..21: MCP spec interop between two real SDKs, through misbehaving middleboxes."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Any

from casebook.mcp_lab import oauth_probe
from casebook.mcp_lab.auth import SCOPE, auth_server
from casebook.mcp_lab.proxy import proxy
from casebook.mcp_lab.servers import TS_DIR, py_server, ts_server

logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("mcp").setLevel(logging.WARNING)


def _inner(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {str(e)[:220]}"


async def _py_call(url: str, mode: str, tool: str = "get_claim", headers: dict[str, str] | None = None) -> dict[str, Any]:
    from mcp import Client

    out: dict[str, Any] = {"client": "py mcp 2.3.0", "mode": mode}
    t0 = time.monotonic()
    first: list[float] = []

    async def progress(p: float, total: float | None, msg: str | None) -> None:
        if not first:
            first.append(round(time.monotonic() - t0, 2))

    try:
        async with asyncio.timeout(15):
            async with Client(url, mode=mode) as c:
                out["protocol"] = c.protocol_version
                out["tools"] = [t.name for t in (await c.list_tools()).tools]
                t0 = time.monotonic()
                r = await c.call_tool(tool, {"claim_id": "4411"}, progress_callback=progress)
                out["result"] = r.content[0].text if r.content else None
                out["call_s"] = round(time.monotonic() - t0, 2)
    except BaseException as e:  # noqa: BLE001
        out["error"] = _inner(e)
    out["first_progress_s"] = first[0] if first else None
    return out


def py_call(url: str, mode: str, tool: str = "get_claim") -> dict[str, Any]:
    return asyncio.run(_py_call(url, mode, tool))


def ts_call(url: str) -> dict[str, Any]:
    p = subprocess.run(["node", str(TS_DIR / "mcp_legacy_client.mjs"), url], capture_output=True, text=True,
                       cwd=TS_DIR, timeout=30)
    return json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"error": p.stderr[-300:]}


def ag16(_: Any = None) -> dict[str, Any]:
    with py_server() as py, ts_server() as ts:
        out = {
            "ts_1_32_client__py_2_3_server": ts_call(py),
            "py_auto__ts_server": py_call(ts, "auto"),
            "py_pinned_2026_07_28__ts_server": py_call(ts, "2026-07-28"),
            "py_pinned_2026_07_28__py_server": py_call(py, "2026-07-28"),
        }
    pinned = out["py_pinned_2026_07_28__ts_server"]
    out["finding"] = {"cls": "mcp.revision_mismatch", "detail": (
        f"old TS client -> new Python server: works ({out['ts_1_32_client__py_2_3_server']['protocol']}, server dual-serves); "
        f"new client auto -> old server: works after a rejected discover probe; new client pinned 2026-07-28 -> old "
        f"server: {pinned.get('error', 'ok')[:110]}")}
    return out


def ag17(_: Any = None) -> dict[str, Any]:
    with py_server() as py:
        with proxy(py, strip=("mcp-method", "mcp-name")) as p1:
            stripped_modern, stripped_auto = py_call(p1, "2026-07-28"), py_call(p1, "auto")
        with proxy(py, set_headers={"mcp-protocol-version": "2025-11-25"}) as p2:
            pinned_version = py_call(p2, "2026-07-28")
    out = {"strip_mcp_method__pinned_modern": stripped_modern, "strip_mcp_method__auto": stripped_auto,
           "gateway_rewrites_protocol_version": pinned_version}
    out["finding"] = {"cls": "mcp.header_mismatch", "detail": (
        f"gateway drops Mcp-Method/Mcp-Name: pinned client -> {stripped_modern.get('error', 'ok')[:70]}; auto client -> "
        f"works but negotiated {stripped_auto.get('protocol')} instead of 2026-07-28 (silent downgrade); "
        f"gateway pins MCP-Protocol-Version -> "
        f"{pinned_version.get('error', 'ok')[:60]} (misleading)")}
    return out


def ag18(_: Any = None) -> dict[str, Any]:
    with py_server() as py:
        direct = py_call(py, "2026-07-28", "slow_report")
        with proxy(py, buffer=True) as p1:
            nginx_with_header = py_call(p1, "2026-07-28", "slow_report")
        with proxy(py, buffer=True, strip_response=("x-accel-buffering",)) as p2:
            nginx_header_lost = py_call(p2, "2026-07-28", "slow_report")
    out = {"direct": direct, "buffering_proxy_header_present": nginx_with_header,
           "buffering_proxy_header_dropped_upstream": nginx_header_lost}
    out["finding"] = {"cls": "mcp.sse_buffered", "detail": (
        f"first progress event: direct {direct['first_progress_s']}s; behind a buffering proxy with X-Accel-Buffering: no "
        f"{nginx_with_header['first_progress_s']}s; header dropped upstream {nginx_header_lost['first_progress_s']}s "
        f"(call took {nginx_header_lost.get('call_s')}s — every update arrives at the end)")}
    return out


def ag19(_: Any = None) -> dict[str, Any]:
    import httpx2

    out: dict[str, Any] = {}
    with auth_server("dcr") as (issuer, _log), py_server(auth_scope=SCOPE, issuer=issuer) as url:
        for tok in (None, "tok-bogus", "tok-wrong-audience", "tok-no-scope", "tok-good"):
            h = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
            if tok:
                h["authorization"] = f"Bearer {tok}"
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "casebook", "version": "1"}}}
            r = httpx2.post(url, json=init, headers=h, timeout=10)
            out[tok or "no_token"] = {"status": r.status_code, "www_authenticate": r.headers.get("www-authenticate")}
        prm = httpx2.get(url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp"), timeout=10).json()
    out["protected_resource_metadata"] = prm
    same_401 = len({out[k]["www_authenticate"] for k in ("no_token", "tok-bogus", "tok-wrong-audience")}) == 1
    out["finding"] = {"cls": "mcp.auth_scope_audience", "detail": (
        f"missing scope -> {out['tok-no-scope']['status']} insufficient_scope; wrong audience -> "
        f"{out['tok-wrong-audience']['status']}, identical to no/bogus token: {same_401}; good token -> {out['tok-good']['status']}")}
    return out


def ag20(_: Any = None) -> dict[str, Any]:
    cimd_url = "https://casebook.example.com/oauth/client.json"
    out: dict[str, Any] = {}
    for shape in ("dcr", "cimd"):
        for label, cm in (("dcr_only_client", None), ("cimd_capable_client", cimd_url)):
            with auth_server(shape) as (issuer, log), py_server(auth_scope=SCOPE, issuer=issuer) as url:
                r = oauth_probe.run(url, cm)
                r["as_requests"] = [entry["path"] for entry in log]
                out[f"as_{shape}__{label}"] = r
    bad = out["as_cimd__dcr_only_client"]
    out["finding"] = {"cls": "mcp.client_registration", "detail": (
        f"AS without registration_endpoint (CIMD only) + DCR-only client -> {bad.get('error', bad['outcome'])[:80]} "
        f"(a guessed /register, never advertised); with a client metadata URL -> client_id="
        f"{out['as_cimd__cimd_capable_client'].get('authorize_client_id')}")}
    return out


def ag21(_: Any = None) -> dict[str, Any]:
    from casebook.mcp_lab.raw_server import raw_server

    with py_server(invalid_tool=True) as py:
        ref_py, ref_ts = py_call(py, "legacy"), ts_call(py)
    with raw_server() as raw:
        third_py, third_ts = py_call(raw, "legacy"), ts_call(raw)
    out = {"reference_server__py_client": ref_py, "reference_server__ts_client": ref_ts,
           "third_party_server__py_client": third_py, "third_party_server__ts_client": third_ts}

    def seen(r: dict[str, Any]) -> str:
        return r["error"].splitlines()[0][:60] if r.get("error") else f"tools={r.get('tools')}"

    out["finding"] = {"cls": "mcp.invalid_tool_definition", "detail": (
        f"one malformed tool -> no tools at all. Reference py server: tools/list fails ({seen(ref_py)}); "
        f"third-party server sending it (handshake era): py 2.3.0 client '{seen(third_py)}', ts 1.32.2 client "
        f"'{seen(third_ts)}' — neither drops just the bad tool")}
    return out


SCENARIOS = {"AG-16": ag16, "AG-17": ag17, "AG-18": ag18, "AG-19": ag19, "AG-20": ag20, "AG-21": ag21}
