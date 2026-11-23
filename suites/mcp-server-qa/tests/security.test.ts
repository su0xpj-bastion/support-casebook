// Secrets, path traversal, tool-description poisoning, auth, and isolation between concurrent calls.
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { call, connect, startHttpServer, stopHttpServer, textOf, TOKEN } from "./helpers.js";

const SECRET = /\bsk-(live|test)-[A-Za-z0-9]{12,}|\bAKIA[0-9A-Z]{16}\b/;

afterAll(stopHttpServer);

describe("data handling", () => {
  let client: Client;
  beforeAll(async () => { client = await connect("stdio"); });
  afterAll(async () => { await client.close(); });

  test("credentials inside incident text are redacted in tool results and resources", async () => {
    for (const id of ["INC-0025", "INC-0040", "INC-0200"]) {
      const out = textOf(await call(client, "get_incident", { id }));
      expect(out).toContain("[REDACTED]");
      expect(out).not.toMatch(SECRET);
      const res = await client.readResource({ uri: `incident://${id}` });
      const c = res.contents[0];
      expect("text" in c ? c.text : "").not.toMatch(SECRET);
    }
  });

  test.each(["../../../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "timeline.md/../../secrets", "/etc/passwd"])(
    "path traversal in read_attachment is refused: %s", async (name) => {
      const r = await call(client, "read_attachment", { incident_id: "INC-0010", name });
      expect(r.isError).toBe(true);
      expect(textOf(r)).not.toContain("root:");
    });

  test("a real attachment still reads", async () => {
    expect(textOf(await call(client, "read_attachment", { incident_id: "INC-0010", name: "timeline.md" }))).toContain("Timeline");
  });

  test("tool descriptions carry no hidden instructions (tool-poisoning lint)", async () => {
    const { tools } = await client.listTools();
    const RED_FLAGS = [/<\/?important>/i, /ignore (all|any|previous|prior)/i, /do not tell the user/i, /~\/\.ssh|id_rsa|\.env\b/i,
                       /before using any tool/i, /[\u{E0000}-\u{E007F}​-‏⁠]/u];
    for (const t of tools) {
      const hits = RED_FLAGS.filter((re) => re.test(`${t.description} ${JSON.stringify(t.inputSchema)}`));
      expect(hits.map(String), `${t.name}`).toEqual([]);
      expect((t.description ?? "").length, `${t.name} description length`).toBeLessThan(600);
    }
  });
});

describe("auth (HTTP)", () => {
  const post = async (url: string, auth?: string) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...(auth ? { Authorization: auth } : {}) },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping" }),
  });

  test("no token: 401 with a WWW-Authenticate challenge", async () => {
    const url = await startHttpServer();
    const res = await post(url);
    expect(res.status).toBe(401);
    expect(res.headers.get("www-authenticate")).toMatch(/^Bearer/);
  });

  test("wrong token: 401, and the token is not echoed back", async () => {
    const url = await startHttpServer();
    const res = await post(url, "Bearer attacker-guess-123456");
    expect(res.status).toBe(401);
    expect(await res.text()).not.toContain("attacker-guess");
  });

  test("valid token: the MCP client connects and calls a tool", async () => {
    const client = await connect("http", TOKEN);
    const r = await call(client, "search_incidents", { query: "auth", limit: 2 });
    expect(r.isError).toBeFalsy();
    await client.close();
  });

  test("an unauthenticated MCP client cannot connect", async () => {
    await expect(connect("http", null)).rejects.toThrow();
  });
});

describe("isolation", () => {
  test.each(["stdio", "http"] as const)("20 concurrent calls each get their own incident (%s)", async (kind) => {
    const client = await connect(kind);
    const ids = Array.from({ length: 20 }, (_, i) => `INC-${String(i + 101).padStart(4, "0")}`);
    const results = await Promise.all(ids.map((id) => call(client, "get_incident", { id })));
    const got = results.map((r) => JSON.parse(textOf(r)).id);
    expect(got).toEqual(ids);
    await client.close();
  });
});
