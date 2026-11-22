# AG-18 — Progress updates all arrive at the end: SSE buffered by a proxy

Keep X-Accel-Buffering: no on SSE responses end to end (or proxy_buffering off for the MCP route). Both reference SDKs send it; the stall happens when a hop drops it.

Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http, https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering
