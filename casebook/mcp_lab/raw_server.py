"""A minimal hand-written handshake-era MCP server (JSON responses, no sessions) that emits a
malformed tool definition on purpose — what a third-party server with a bug sends (AG-21).
The reference SDK servers refuse to produce this, so it has to be written by hand."""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from casebook.mcp_lab.servers import _wait, free_port

TOOLS = [
    {"name": "get_claim", "description": "Fetch a claim.",
     "inputSchema": {"type": "object", "properties": {"claim_id": {"type": "string"}}, "required": ["claim_id"]}},
    {"name": "broken_tool", "description": "Malformed schema.", "inputSchema": {"type": "objekt"}},
]


async def mcp(request: Request) -> Response:
    msg: dict[str, Any] = await request.json()
    if "id" not in msg:
        return Response(status_code=202)
    method = msg.get("method")
    if method == "initialize":
        result: dict[str, Any] = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "third-party-claims", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": f"claim {msg['params']['arguments'].get('claim_id')}: open"}]}
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
    return JSONResponse({"jsonrpc": "2.0", "id": msg["id"], "result": result})


@contextlib.contextmanager
def raw_server() -> Iterator[str]:
    port = free_port()
    app = Starlette(routes=[Route("/mcp", mcp, methods=["POST"])])
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    _wait(port)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        srv.should_exit = True
        t.join(timeout=5)
