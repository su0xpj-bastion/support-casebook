"""Real MCP servers for the interop packs (AG-16..21).

  py  mcp 2.3.0 (Python reference SDK): speaks 2026-07-28 and dual-serves the 2025 handshake era
  ts  @modelcontextprotocol/sdk 1.32.2 (TypeScript): handshake era only (2025-11-25), run via ts/mcp_legacy_server.mjs
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from mcp.server.mcpserver import Context, MCPServer

TS_DIR = Path(__file__).resolve().parents[2] / "ts"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_py_app(*, auth_scope: str | None = None, issuer: str | None = None, resource: str | None = None,
                 invalid_tool: bool = False) -> Any:
    kwargs: dict[str, Any] = {}
    if auth_scope:
        from casebook.mcp_lab.auth import auth_settings, verifier

        kwargs.update(token_verifier=verifier(resource), auth=auth_settings(auth_scope, resource, issuer))
    server = MCPServer("claims", **kwargs)

    @server.tool()
    def get_claim(claim_id: str) -> str:
        """Fetch a claim."""
        return f"claim {claim_id}: open"

    @server.tool()
    async def slow_report(claim_id: str, ctx: Context) -> str:
        """Long job that reports progress every 0.4 s."""
        for step in range(1, 5):
            await ctx.report_progress(step, 4, f"step {step}")
            await asyncio.sleep(0.4)
        return f"report for {claim_id} ready"

    if invalid_tool:
        _add_invalid_tool(server)
    return server.streamable_http_app()


def _add_invalid_tool(server: Any) -> None:
    """Register a tool whose inputSchema is not a valid JSON Schema object (type 'objekt')."""
    @server.tool(name="broken_tool")
    def broken_tool(x: str) -> str:
        """A tool with a malformed schema."""
        return x

    tm = server._tool_manager  # noqa: SLF001 — deliberate: the SDK refuses to build a bad schema the normal way
    tool = tm.get_tool("broken_tool")
    tool.parameters = {"type": "objekt", "properties": {"x": {"type": "string"}}}


@contextlib.contextmanager
def py_server(**kw: Any) -> Iterator[str]:
    port = free_port()
    if kw.get("auth_scope"):
        kw["resource"] = f"http://127.0.0.1:{port}/mcp"
    config = uvicorn.Config(build_py_app(**kw), host="127.0.0.1", port=port, log_level="error", lifespan="on")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    _wait(port)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        srv.should_exit = True
        t.join(timeout=5)


@contextlib.contextmanager
def ts_server() -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen(["node", str(TS_DIR / "mcp_legacy_server.mjs"), str(port)], cwd=TS_DIR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wait(port: int, timeout: float = 10) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"server on {port} did not start")
