import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import type { Server } from "node:http";

export const TOKEN = "qa-test-token-0123456789";          // test value for the local server only
process.env.MCP_TOKEN = TOKEN;
process.env.MCP_TEST = "1";

export type Kind = "stdio" | "http";
export const KINDS: Kind[] = ["stdio", "http"];

let http: Server | undefined;
let httpUrl = "";

export async function startHttpServer(): Promise<string> {
  if (!http) {
    const { startHttp } = await import("../src/http.js");
    http = await startHttp(0);
    const addr = http.address();
    httpUrl = `http://127.0.0.1:${typeof addr === "object" && addr ? addr.port : 0}/mcp`;
  }
  return httpUrl;
}

export async function stopHttpServer() {
  await new Promise<void>((r) => (http ? http.close(() => r()) : r()));
  http = undefined;
}

export async function connect(kind: Kind, token: string | null = TOKEN): Promise<Client> {
  const client = new Client({ name: "mcp-server-qa", version: "1.0.0" });
  if (kind === "stdio") {
    await client.connect(new StdioClientTransport({
      command: process.execPath, args: ["--import", "tsx", "src/stdio.ts"],
      env: { ...process.env, MCP_TEST: "1" } as Record<string, string>, stderr: "pipe",
    }));
  } else {
    const url = await startHttpServer();
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    await client.connect(new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers } }));
  }
  return client;
}

export async function call(client: Client, name: string, args: Record<string, unknown>): Promise<CallToolResult> {
  return (await client.callTool({ name, arguments: args })) as CallToolResult;
}

export const textOf = (r: CallToolResult) =>
  r.content.map((c) => (c.type === "text" ? c.text : "")).join("");
