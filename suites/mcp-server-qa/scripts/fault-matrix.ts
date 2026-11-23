// Run the suite once per injected server bug and check that exactly the pinned tests fail.
//   npx tsx scripts/fault-matrix.ts            verify against tests/faults.json (exit 1 on mismatch)
//   npx tsx scripts/fault-matrix.ts --record   print what each fault breaks
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

type Expect = { faults: Record<string, { what: string; caught_by: string[] }> };
const record = process.argv.includes("--record");
const spec = JSON.parse(readFileSync("tests/faults.json", "utf8")) as Expect;

function failing(fault: string): string[] {
  const file = `reports/vitest-${fault || "healthy"}.json`;
  spawnSync(process.execPath, ["node_modules/vitest/vitest.mjs", "run", "--reporter=json", `--outputFile=${file}`],
    { env: { ...process.env, MCP_FAULTS: fault }, encoding: "utf8", maxBuffer: 64 << 20 });
  const report = JSON.parse(readFileSync(file, "utf8"));
  const out = new Set<string>();
  for (const file of report.testResults) {
    for (const t of file.assertionResults) {
      if (t.status === "failed") out.add(`${file.name.replace(/^.*\//, "")}: ${t.fullName.replace(/^(stdio|http) /, "")}`);
    }
  }
  return [...out].sort();
}

mkdirSync("reports", { recursive: true });
let mismatches = 0;
const rows: string[] = [];
for (const fault of ["", ...Object.keys(spec.faults)]) {
  const got = failing(fault);
  const want = fault ? [...spec.faults[fault].caught_by].sort() : [];
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) mismatches++;
  console.log(`${ok ? "OK  " : "DIFF"} ${fault || "(healthy)"}: ${got.length} failing`);
  if (!ok || record) for (const g of got) console.log(`       ${want.includes(g) ? " " : "+"} ${g}`);
  if (!ok) for (const w of want.filter((w) => !got.includes(w))) console.log(`       - ${w}`);
  rows.push(`| \`${fault || "(healthy)"}\` | ${fault ? spec.faults[fault].what : "no fault"} | ${got.length ? got.join("<br>") : "nothing"} | ${ok ? "✅" : "❌"} |`);
}
mkdirSync("reports", { recursive: true });
const md = ["| Fault | Bug | Caught by | |", "|---|---|---|---|", ...rows].join("\n");
writeFileSync("reports/fault-matrix.md", md + "\n");
if (process.env.GITHUB_STEP_SUMMARY) writeFileSync(process.env.GITHUB_STEP_SUMMARY, `## Fault matrix\n\n${md}\n`, { flag: "a" });
process.exit(record || !mismatches ? 0 : 1);
