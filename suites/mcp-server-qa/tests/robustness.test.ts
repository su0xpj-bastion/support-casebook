// Fuzzing, progress + cancellation, and schema drift against the committed tool baseline.
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import fc from "fast-check";
import { readFileSync } from "node:fs";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { call, connect, textOf } from "./helpers.js";
import { diffTools, snapshotTools, type ToolSnapshot } from "../scripts/schema-snapshot.js";

let client: Client;
beforeAll(async () => { client = await connect("stdio"); });
afterAll(async () => { await client.close(); });

describe("fuzzing", () => {
  const anyJson = fc.oneof(fc.string(), fc.integer(), fc.double(), fc.boolean(), fc.constant(null),
                           fc.array(fc.string(), { maxLength: 3 }), fc.string({ minLength: 300, maxLength: 2000 }));
  test.each(["search_incidents", "get_incident", "read_attachment", "create_ticket"])(
    "%s survives 150 random argument sets and the server stays up", async (name) => {
      await fc.assert(fc.asyncProperty(fc.dictionary(fc.constantFrom("query", "limit", "cursor", "id", "incident_id",
        "name", "title", "severity", "body", "extra"), anyJson, { maxKeys: 4 }), async (args) => {
        try {
          const r = await call(client, name, args);
          expect(Array.isArray(r.content)).toBe(true);           // a well-formed result...
        } catch (err) {
          expect(String((err as { code?: number }).code)).toMatch(/^-32\d{3}$/);   // ...or a JSON-RPC error
        }
      }), { numRuns: 150, seed: 20261116 });
      await expect(client.ping()).resolves.toBeDefined();
    }, 60_000);
});

describe("long-running tools", () => {
  test("export_report streams progress notifications", async () => {
    const progress: number[] = [];
    const r = await client.callTool({ name: "export_report", arguments: { days: 7 } }, undefined,
                                    { onprogress: (p) => progress.push(p.progress), timeout: 10_000 });
    expect(textOf(r as never)).toContain("Report for 7 days");
    expect(progress.length).toBeGreaterThanOrEqual(10);
    expect(progress).toEqual([...progress].sort((a, b) => a - b));
  });

  test("cancelling export_report stops the work on the server", async () => {
    const ctrl = new AbortController();
    const pending = client.callTool({ name: "export_report", arguments: { days: 30 } }, undefined,
                                    { signal: ctrl.signal, onprogress: () => {} });
    setTimeout(() => ctrl.abort(), 200);
    await expect(pending).rejects.toThrow();
    await new Promise((r) => setTimeout(r, 1300));             // longer than the whole export
    const stats = JSON.parse(textOf(await call(client, "_test_stats", {})));
    expect(stats.exportStepsAfterCancel).toBe(0);
    await expect(client.ping()).resolves.toBeDefined();
  });
});

describe("schema drift", () => {
  test("tool schemas are backward compatible with the committed baseline", async () => {
    const baseline = JSON.parse(readFileSync("tests/tool-schema.baseline.json", "utf8")) as ToolSnapshot;
    const { tools } = await client.listTools();
    const { breaking } = diffTools(baseline, snapshotTools(tools));
    expect(breaking).toEqual([]);
  });

  test("the drift checker flags removed tools, new required params and type changes, not additions", () => {
    const base: ToolSnapshot = { a: { required: ["x"], props: { x: "string" } }, b: { required: [], props: {} } };
    const next: ToolSnapshot = { a: { required: ["x", "y"], props: { x: "number", y: "string", z: "string" } },
                                 c: { required: [], props: {} } };
    const { breaking, additive } = diffTools(base, next);
    expect(breaking.sort()).toEqual(["a: param x changed type string -> number", "a: new required param y", "b: tool removed"].sort());
    expect(additive.sort()).toEqual(["a: new optional param z", "c: new tool"].sort());
  });
});
