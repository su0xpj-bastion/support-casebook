// Snapshot a server's tool schemas and diff them for breaking changes.
//   npx tsx scripts/schema-snapshot.ts > tests/tool-schema.baseline.json
export type ToolSnapshot = Record<string, { required: string[]; props: Record<string, string> }>;

type ListedTool = { name: string; inputSchema: { properties?: Record<string, { type?: string | string[] }>; required?: string[] } };

export function snapshotTools(tools: ListedTool[]): ToolSnapshot {
  const out: ToolSnapshot = {};
  for (const t of [...tools].sort((a, b) => a.name.localeCompare(b.name))) {
    if (t.name.startsWith("_")) continue;                         // test-only tools
    const props = Object.fromEntries(Object.entries(t.inputSchema.properties ?? {})
      .map(([k, v]) => [k, Array.isArray(v.type) ? v.type.join("|") : (v.type ?? "any")]).sort());
    out[t.name] = { required: [...(t.inputSchema.required ?? [])].sort(), props };
  }
  return out;
}

export function diffTools(base: ToolSnapshot, next: ToolSnapshot) {
  const breaking: string[] = [];
  const additive: string[] = [];
  for (const [name, b] of Object.entries(base)) {
    const n = next[name];
    if (!n) { breaking.push(`${name}: tool removed`); continue; }
    for (const r of n.required) if (!b.required.includes(r)) breaking.push(`${name}: new required param ${r}`);
    for (const [p, type] of Object.entries(b.props)) {
      if (!(p in n.props)) breaking.push(`${name}: param ${p} removed`);
      else if (n.props[p] !== type) breaking.push(`${name}: param ${p} changed type ${type} -> ${n.props[p]}`);
    }
    for (const p of Object.keys(n.props)) if (!(p in b.props) && !n.required.includes(p)) additive.push(`${name}: new optional param ${p}`);
  }
  for (const name of Object.keys(next)) if (!(name in base)) additive.push(`${name}: new tool`);
  return { breaking, additive };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StdioClientTransport } = await import("@modelcontextprotocol/sdk/client/stdio.js");
  const client = new Client({ name: "snapshot", version: "1.0.0" });
  await client.connect(new StdioClientTransport({ command: process.execPath, args: ["--import", "tsx", "src/stdio.ts"] }));
  const { tools } = await client.listTools();
  console.log(JSON.stringify(snapshotTools(tools as ListedTool[]), null, 2));
  await client.close();
}
