// Handshake-era MCP server on the official TypeScript SDK 1.32.2 (protocol 2025-11-25):
// stateful sessions (Mcp-Session-Id), GET stream for server messages.
import { randomUUID } from "node:crypto";
import http from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const port = Number(process.argv[2] || 3333);
const transports = new Map();

function newServer() {
  const server = new McpServer({ name: "claims-legacy", version: "1.0.0" });
  server.registerTool("get_claim", { description: "Fetch a claim.", inputSchema: { claim_id: z.string() } },
    async ({ claim_id }) => ({ content: [{ type: "text", text: `claim ${claim_id}: open` }] }));
  return server;
}

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString() || "null") : undefined;
  const sid = req.headers["mcp-session-id"];
  let transport = sid ? transports.get(sid) : undefined;
  if (!transport) {
    if (req.method !== "POST" || !body || body.method !== "initialize") {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", error: { code: -32000, message: "Bad Request: No valid session ID provided" }, id: null }));
      return;
    }
    transport = new StreamableHTTPServerTransport({ sessionIdGenerator: () => randomUUID(),
      onsessioninitialized: (id) => transports.set(id, transport) });
    await newServer().connect(transport);
  }
  await transport.handleRequest(req, res, body);
}).listen(port, "127.0.0.1");
