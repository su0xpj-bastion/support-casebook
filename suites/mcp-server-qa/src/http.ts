// Streamable HTTP transport, stateless: a fresh server + transport per request, bearer-token auth.
import { createServer as createHttp, type IncomingMessage, type ServerResponse } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createServer, FAULTS } from "./server.js";

const PORT = Number(process.env.PORT || 8765);
const TOKEN = process.env.MCP_TOKEN || "";

function authorized(req: IncomingMessage): boolean {
  if (FAULTS.has("no_auth")) return true;
  const got = Buffer.from((req.headers.authorization || "").replace(/^Bearer /, ""));
  const want = Buffer.from(TOKEN);
  return TOKEN.length > 0 && got.length === want.length && timingSafeEqual(got, want);
}

function deny(req: IncomingMessage, res: ServerResponse) {
  const shown = FAULTS.has("token_in_error") ? ` (got "${req.headers.authorization || ""}")` : "";
  res.writeHead(401, { "Content-Type": "application/json", "WWW-Authenticate": 'Bearer realm="incident-desk"' });
  res.end(JSON.stringify({ jsonrpc: "2.0", error: { code: -32001, message: `Unauthorized${shown}` }, id: null }));
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : undefined;
}

export function startHttp(port = PORT) {
  const http = createHttp(async (req, res) => {
    if (req.url === "/healthz") { res.writeHead(200).end("ok"); return; }
    if (req.url !== "/mcp") { res.writeHead(404).end(); return; }
    if (!authorized(req)) return deny(req, res);
    if (req.method !== "POST") { res.writeHead(405, { Allow: "POST" }).end(); return; }
    let body: unknown;
    try { body = await readBody(req); } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", error: { code: -32700, message: "Parse error" }, id: null }));
      return;
    }
    const server = createServer();
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => { transport.close(); server.close(); });
    await server.connect(transport);
    await transport.handleRequest(req, res, body);
  });
  return new Promise<ReturnType<typeof createHttp>>((resolve) => http.listen(port, "127.0.0.1", () => resolve(http)));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const s = await startHttp();
  const addr = s.address();
  console.log(`incident-desk MCP on http://127.0.0.1:${typeof addr === "object" && addr ? addr.port : PORT}/mcp`);
}
