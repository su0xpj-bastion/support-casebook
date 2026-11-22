"""A small reverse proxy that misbehaves the way real middleboxes do (AG-17, AG-18).

  strip      request headers to drop (e.g. a gateway allowlist that doesn't know Mcp-Method / Mcp-Name)
  set        request headers to overwrite (e.g. a gateway pinning MCP-Protocol-Version)
  buffer     hold the whole upstream response before forwarding, unless the upstream sent
             `X-Accel-Buffering: no` — the documented nginx behaviour (proxy_buffering on by default)
  strip_response  response headers removed before this hop decides (e.g. a gateway in front that
             drops headers it doesn't know, including X-Accel-Buffering)

This emulates nginx's buffering decision; it is not nginx (no nginx on this machine). The pack says so.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

import httpx2
import uvicorn
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from casebook.mcp_lab.servers import _wait, free_port

HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}


def build_proxy(upstream: str, *, strip: tuple[str, ...] = (), set_headers: dict[str, str] | None = None,
                buffer: bool = False, strip_response: tuple[str, ...] = (),
                log: list[dict[str, Any]] | None = None) -> Any:
    client = httpx2.AsyncClient(timeout=30)
    strip_l = {h.lower() for h in strip}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        req = Request(scope, receive)
        body = await req.body()
        headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP and k.lower() not in strip_l}
        headers.update(set_headers or {})
        if log is not None:
            log.append({"method": req.method, "headers_forwarded": sorted(headers)})
        up = client.build_request(req.method, upstream, content=body, headers=headers)
        resp = await client.send(up, stream=True)
        drop = {h.lower() for h in strip_response}
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP and k.lower() not in drop}
        # an upstream layer that drops the header (strip_response) means this hop never sees it
        honour = "x-accel-buffering" not in drop and resp.headers.get("x-accel-buffering", "").lower() == "no"
        if buffer and not honour:
            data = await resp.aread()
            await resp.aclose()
            await Response(content=data, status_code=resp.status_code, headers=out_headers)(scope, receive, send)
            return

        async def gen() -> Any:
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()

        await StreamingResponse(gen(), status_code=resp.status_code, headers=out_headers)(scope, receive, send)

    return app


@contextlib.contextmanager
def proxy(upstream: str, **kw: Any) -> Iterator[str]:
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(build_proxy(upstream, **kw), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    _wait(port)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        srv.should_exit = True
        t.join(timeout=5)
