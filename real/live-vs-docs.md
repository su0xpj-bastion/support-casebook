# Live vs. docs, SDKs vs. docs — found while building this repo

Each line is backed by a pack that reproduces it; dates are when observed.

| Observed | Pack | Where |
|---|---|---|
| Both official SDK families (Python and Node, OpenAI and Anthropic) retry every 429 twice by default, including documented non-retryable billing codes; anthropic 2.9.0 raises a dedicated `EnforcedSpendLimitReachedError` and still retries it | OA-04..06, AN-01 | mock |
| The real Anthropic 401 carries `x-should-retry: false`; the real OpenAI 401 has no `x-should-retry` but carries a base64 copy of the error body in `x-error-json` | AN-13, OA-07 | live, 2026-11-19 |
| A 33 MiB request with an invalid key returns 401, not the documented edge 413 | AN-16 | live, 2026-11-19 |
| anthropic 2.9.0 removed `temperature` from `messages.create()` (TypeError, nothing sent) | AN-21 | installed SDK |
| anthropic 2.9.0 warns on Sonnet 4.5's end of life via `DeprecationWarning`, which Python hides outside `__main__` | AN-26 | installed SDK |
| A mid-stream overload arrives as a generic `APIStatusError` (status 200), not `OverloadedError` | AN-09 | mock |
| Claude Code 2.1.329 prints `apiKeyHelper failed: exited 1: …` (docs: "Your apiKeyHelper script is failing") and still sends the request with an empty Bearer | AN-55 | real CLI |
| A headless Claude Agent SDK run whose only tool call was denied reports `subtype: success, is_error: false` | AG-22 | real SDK |
| Codex 0.171.0 behind an untrusted CA loops on "Reconnecting... waiting for network" | OA-48 | real CLI |
| A header-stripping gateway silently downgrades MCP `auto` clients to 2025-11-25 | AG-17 | two real SDKs |
| One malformed tool definition makes both reference MCP clients reject the whole tool list | AG-21 | two real SDKs |
| signxml 5.3.0 reports an expired IdP certificate as `InvalidSignature` | identity lab | library |
| 2 of 53 doc URLs cited in this repo were 404 — both hand-written paths, fixed | tools/doc_links.py | live, 2026-11-21 |

None of these has been filed with the vendors. Filing is an outward-facing step for the repo owner to decide.
