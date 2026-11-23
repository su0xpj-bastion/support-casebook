# mcp-server-qa

> Part of [support-casebook](../../README.md). Run every command from `suites/mcp-server-qa/`. CI: [`suite-mcp-server-qa.yml`](../../.github/workflows/suite-mcp-server-qa.yml), which runs when this folder changes.

A real **MCP server** in TypeScript (`incident-desk`: search, read, ticket, close and export incidents for on-call work) and the QA suite that proves it is safe to hand to an agent. The suite drives the server through the **official MCP TypeScript client** over **both transports** (stdio and Streamable HTTP) and covers protocol conformance, input validation, property-based fuzzing, auth, pagination, progress and cancellation, secret redaction, path traversal, tool-description poisoning, tool annotations, isolation under concurrency, and schema drift against a committed baseline. Twelve deliberate server bugs prove each check works.

```bash
npm ci
npx vitest run                  # 51 tests, ~5 s, stdio + HTTP
npx tsx scripts/fault-matrix.ts # 12 injected bugs, each caught by exactly its tests
npm run server:stdio            # run the server for a client (see docs/CLIENTS.md)
MCP_TOKEN=... npm run server:http
```

## The server

| Tool | Notes | Annotations |
|---|---|---|
| `search_incidents` | text search, ≤ 50 per page, `nextCursor`, typed `structuredContent` against an `outputSchema` | read-only |
| `get_incident` | one incident; credential-shaped strings redacted before they leave the server | read-only |
| `read_attachment` | file names validated: no `..`, no slashes, only files attached to that incident | read-only |
| `create_ticket` | follow-up ticket | write |
| `close_incident` | closes an incident ("cannot be undone") | **destructive** |
| `export_report` | slow (20 steps); sends `notifications/progress` and stops when cancelled | read-only |

Also a resource template `incident://{id}`, a static `catalog://services` resource, and a `triage_incident` prompt. HTTP mode is stateless and requires a bearer token, compared in constant time, with a `WWW-Authenticate` challenge on 401. The data deliberately includes credentials (to be redacted) and one incident whose text tells AI assistants to "close every open incident" (data, not instructions).

## What the suite checks

| Area | Tests | Over |
|---|---|---|
| Conformance | server identity and capabilities; every tool has a name, description and object schema; resources (static and template); prompts with arguments; ping; structured output matches the declared output schema | stdio + HTTP |
| Validation | wrong types, missing fields and bad enums return **-32602** or `isError`, never a success; a missing record is `isError: true`; an unknown tool is an error | stdio + HTTP |
| Limits | at most 50 results per call; walking `nextCursor` returns all 250 incidents exactly once | stdio + HTTP |
| Annotations | read tools are `readOnlyHint`, `close_incident` is `destructiveHint`, so a client knows what to ask approval for | stdio + HTTP |
| Fuzzing | fast-check: 150 random argument sets per tool (wrong types, huge strings, nulls, extra keys); every reply is a valid result or a JSON-RPC error, and the server still answers `ping` | stdio |
| Progress and cancellation | progress notifications arrive in order; cancelling the export stops the work **on the server** (checked through a test-only counter), and the server keeps serving | stdio |
| Secrets | `sk-live-…` and `AKIA…` keys inside incident text never reach the client, through tools or resources | stdio |
| Path traversal | `../../etc/passwd`, URL-encoded and nested variants are refused | stdio |
| Tool poisoning | descriptions are linted for hidden instructions (`<IMPORTANT>`, "ignore previous", "do not tell the user", `~/.ssh`, zero-width and Unicode tag characters) and length | stdio |
| Auth | no token and wrong token → 401 with a challenge; the token is never echoed; an unauthenticated MCP client cannot connect | HTTP |
| Isolation | 20 concurrent `get_incident` calls each get their own record | stdio + HTTP |
| Schema drift | tool schemas diffed against `tests/tool-schema.baseline.json`: a removed tool, a new required parameter or a changed type fails; new optional parameters and new tools pass | stdio |

## The fault matrix

`MCP_FAULTS=<name>` switches on one real-world server bug; `scripts/fault-matrix.ts` runs the suite once per fault and fails unless **exactly** the tests pinned in [`tests/faults.json`](tests/faults.json) fail.

| Fault | The bug | Caught by |
|---|---|---|
| `lenient_schema` | input schemas accept anything | the 4 invalid-input tests, schema drift |
| `error_as_success` | errors returned with `isError: false` | missing-record test, path-traversal tests |
| `unbounded_results` | page size ignored | paging test, structured-output test |
| `missing_annotations` | no read-only / destructive hints | annotations test |
| `leaks_secrets` | no redaction | secrets test |
| `path_traversal` | `..` followed | 3 traversal tests (the absolute-path case is still refused by the name check) |
| `poisoned_description` | hidden instructions in a tool description | tool-poisoning lint |
| `no_auth` | HTTP endpoint open | 3 auth tests |
| `token_in_error` | 401 echoes the caller's token | wrong-token test |
| `ignores_cancel` | cancelled export keeps running | cancellation test |
| `crosstalk` | concurrent calls share a variable | isolation tests, both transports |
| `schema_breaking_change` | `get_incident` gains a required `tenant` | schema drift, and every test that calls `get_incident` the old way |

Building the matrix caught a bug in the suite itself: the first version of the invalid-input test counted *any* error message containing "invalid" as a rejection, so with `lenient_schema` on it still passed, matching an unrelated output-validation error. The test now accepts only the JSON-RPC invalid-params code (-32602) or an `isError` result.

## Real clients

[`docs/CLIENTS.md`](docs/CLIENTS.md) has the configs for Claude Code, Codex CLI, the Claude API MCP connector and the OpenAI Responses remote MCP tool, and what to check in a live cross-client run (approval on the destructive tool, the injected incident text, no secrets in the transcript).

## Layout

```
src/server.ts                 the MCP server (tools, resources, prompt) + 12 faults
src/http.ts, src/stdio.ts     transports; HTTP is stateless with bearer auth
src/data.ts                   250 deterministic incidents, attachments, redaction
tests/protocol.test.ts        conformance, validation, limits, annotations (both transports)
tests/security.test.ts        secrets, traversal, tool poisoning, auth, isolation
tests/robustness.test.ts      fuzzing, progress + cancellation, schema drift
scripts/schema-snapshot.ts    snapshot tools/list and diff for breaking changes
scripts/fault-matrix.ts       runs and checks the fault matrix
```

## Where this stops

- **Not yet run under real agent clients.** The suite uses the official MCP client library, which proves protocol behaviour, not how Claude Code or Codex present tools or ask for approval. That is the live step in `docs/CLIENTS.md`.
- **Cancellation is tested over stdio.** In stateless HTTP mode each request gets its own server instance, so a cancel notification arrives at a different instance; long-running work over HTTP needs a stateful session (or task IDs) to be cancellable, which this server doesn't implement.
- **The tool-poisoning lint is a pattern list**, good at the known shapes of hidden instructions, not a guarantee. It is meant to run in CI on every change to tool descriptions.
- **Auth is a static bearer token**, enough to test the 401 path. Production remote servers should use the MCP authorization spec (OAuth 2.1 with protected-resource metadata).
