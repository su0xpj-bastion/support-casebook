# AG-16 — MCP revision mismatch: 2026-07-28 client vs 2025-11-25 server

Clients: use mode 'auto' (probe, then fall back) rather than pinning 2026-07-28 until servers upgrade. Servers: dual-serve both eras, as the Python reference SDK does.

Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
