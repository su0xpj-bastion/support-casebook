// Handshake-era MCP client on the official TypeScript SDK 1.32.2. Prints one JSON line.
//   node ts/mcp_legacy_client.mjs <url>
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const url = process.argv[2];
const out = { sdk: "ts-1.32.2", connected: false, protocol: null, tools: null, call: null, error: null };
const client = new Client({ name: "casebook-legacy-client", version: "1.0.0" });
const transport = new StreamableHTTPClientTransport(new URL(url));
const timer = setTimeout(() => { out.error = "timeout after 8s"; process.stdout.write(JSON.stringify(out) + "\n"); process.exit(0); }, 8000);
try {
  await client.connect(transport);
  out.connected = true;
  out.protocol = transport.protocolVersion ?? null;
  const tools = await client.listTools();
  out.tools = tools.tools.map((t) => t.name);
  const r = await client.callTool({ name: "get_claim", arguments: { claim_id: "4411" } });
  out.call = r.content?.[0]?.text ?? null;
} catch (e) {
  out.error = `${e?.constructor?.name}: ${String(e?.message ?? e).slice(0, 240)}`;
}
clearTimeout(timer);
await client.close().catch(() => {});
process.stdout.write(JSON.stringify(out) + "\n");
process.exit(0);
