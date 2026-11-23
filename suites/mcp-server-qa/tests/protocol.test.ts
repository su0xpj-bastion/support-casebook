// Conformance, validation, limits and annotations: run over BOTH transports.
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { call, connect, KINDS, stopHttpServer, textOf } from "./helpers.js";

afterAll(stopHttpServer);

describe.each(KINDS)("%s", (kind) => {
  let client: Client;
  beforeAll(async () => { client = await connect(kind); });
  afterAll(async () => { await client.close(); });

  // ---------------- conformance ----------------
  test("initialize: server identity and capabilities", () => {
    expect(client.getServerVersion()).toMatchObject({ name: "incident-desk", version: expect.stringMatching(/^\d+\.\d+\.\d+$/) });
    const caps = client.getServerCapabilities()!;
    expect(caps.tools).toBeDefined();
    expect(caps.resources).toBeDefined();
    expect(caps.prompts).toBeDefined();
  });

  test("tools/list: every tool has a name, a description and an object input schema", async () => {
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).toEqual(expect.arrayContaining(
      ["search_incidents", "get_incident", "read_attachment", "create_ticket", "close_incident", "export_report"]));
    for (const t of tools) {
      expect(t.name).toMatch(/^_?[a-z][a-z0-9_]{2,63}$/);
      expect(t.description?.length, `${t.name} description`).toBeGreaterThan(10);
      expect(t.inputSchema.type).toBe("object");
    }
  });

  test("resources: list, template and read", async () => {
    const { resources } = await client.listResources();
    expect(resources.map((r) => r.uri)).toContain("catalog://services");
    const { resourceTemplates } = await client.listResourceTemplates();
    expect(resourceTemplates.map((r) => r.uriTemplate)).toContain("incident://{id}");
    const res = await client.readResource({ uri: "incident://INC-0003" });
    const c = res.contents[0];
    expect(JSON.parse("text" in c ? c.text : "{}").id).toBe("INC-0003");
  });

  test("prompts: list and get with arguments", async () => {
    const { prompts } = await client.listPrompts();
    expect(prompts.map((p) => p.name)).toContain("triage_incident");
    const p = await client.getPrompt({ name: "triage_incident", arguments: { id: "INC-0009" } });
    expect(JSON.stringify(p.messages)).toContain("INC-0009");
  });

  test("ping", async () => {
    await expect(client.ping()).resolves.toBeDefined();
  });

  test("structured output matches the declared output schema", async () => {
    const r = await call(client, "search_incidents", { query: "billing", limit: 3 });
    const s = r.structuredContent as { items: { id: string }[]; nextCursor?: string };
    expect(s.items.length).toBeLessThanOrEqual(3);
    expect(s.items.every((i) => /^INC-\d{4}$/.test(i.id))).toBe(true);
  });

  // ---------------- validation ----------------
  test.each([
    ["search_incidents", { query: 123 }],
    ["search_incidents", { query: "x", limit: "ten" }],
    ["search_incidents", {}],
    ["get_incident", { id: 42 }],
    ["create_ticket", { title: "", severity: "S9" }],
  ])("invalid input to %s is rejected with an error: %j", async (name, args) => {
    let rejected = false;
    try {
      const r = await call(client, name, args);
      rejected = r.isError === true;
    } catch (err) {
      rejected = String((err as { code?: number }).code) === "-32602";      // invalid params, nothing broader
    }
    expect(rejected).toBe(true);
  });

  test("a missing record is an error result, not a success", async () => {
    const r = await call(client, "get_incident", { id: "INC-9999" });
    expect(r.isError).toBe(true);
    expect(textOf(r)).toMatch(/not found/i);
  });

  test("an unknown tool is an error, never a silent success", async () => {
    try {
      const r = await call(client, "drop_database", {});
      expect(r.isError).toBe(true);
      expect(textOf(r)).toMatch(/not found/i);
    } catch (err) {
      expect(String(err)).toMatch(/not found|-32601|-32602/i);
    }
  });

  // ---------------- limits ----------------
  test("results are paged: at most 50 per call, cursor walks every match exactly once", async () => {
    const seen = new Set<string>();
    let cursor: string | undefined;
    let pages = 0;
    do {
      const r = await call(client, "search_incidents", { query: "on", limit: 50, ...(cursor ? { cursor } : {}) });
      const s = r.structuredContent as { items: { id: string }[]; nextCursor?: string };
      expect(s.items.length).toBeLessThanOrEqual(50);
      for (const i of s.items) { expect(seen.has(i.id)).toBe(false); seen.add(i.id); }
      cursor = s.nextCursor;
      pages += 1;
    } while (cursor && pages < 20);
    expect(pages).toBeGreaterThan(1);
    expect(seen.size).toBe(250);
  });

  // ---------------- annotations ----------------
  test("tool annotations tell clients which tools are read-only and which are destructive", async () => {
    const { tools } = await client.listTools();
    const by = Object.fromEntries(tools.map((t) => [t.name, t.annotations ?? {}]));
    for (const name of ["search_incidents", "get_incident", "read_attachment", "export_report"]) {
      expect(by[name].readOnlyHint, `${name} readOnlyHint`).toBe(true);
    }
    expect(by.close_incident.destructiveHint).toBe(true);
    expect(by.create_ticket.readOnlyHint).toBe(false);
  });
});
