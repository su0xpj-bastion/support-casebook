# Glossary

| Term | Meaning here |
|---|---|
| **Pack** | One reproducible support failure: `faults/<provider>/<ID>-<slug>/` with `pack.yaml`, `expected.json`, `fix.md`, and optionally `live/` captures |
| **Pack id prefixes** | `OA` OpenAI API, `AN` Anthropic (API, Claude Code, enterprise), `AG` agents, tools and MCP, `ID` generic identity (SAML/OAuth/OIDC), `SIM` incident simulation |
| **Kind** | `error` (an HTTP error the SDK raises), `anomaly` (HTTP 200 or client-side behaviour that's still a failure), `sim` |
| **Support Packet** | The evidence record for one failure: request ids, status, error fields, rate headers, org/project, SDK, redaction count, warnings. Schema: `casebook/schema/packet.schema.json` |
| **Verdict** | Output of `rules.classify`: class, retryable, owner, basis, confidence, doc link |
| **Retryable Y / N / C** | Y: retry with backoff. N: retrying can't help; fix config or billing first. C: conditional; depends on evidence the packet doesn't have |
| **Owner** | Who must act: `customer`, `customer-admin`, `provider`, `network`, or `support` (unknown) |
| **Confidence** | How the verdict was reached: `code` (documented field), `message-prefix`, `header-heuristic`, `status-only`, `client-side`. Message and header verdicts are fragile because wording can change |
| **Provenance tag** | Per field in `expected.json`: `doc-verbatim` (quoted in docs), `doc-partial`, `synthesized` (shape documented, wording invented), `live-captured`. A pack's provenance is its weakest field |
| **Look-alike** | A pack with the same status code as another but a different fix (`looks_like` in `pack.yaml`) |
| **SDK default** | The provider SDK's own retry behaviour: 2 retries (3 requests) on 429 and 5xx |
| **Policy** | `casebook.policy`: SDK retries off, retry decided per attempt from the verdict |
| **Amplification** | Requests sent ÷ logical calls (user actions). 1.0 means no retries |
| **Retry budget** | A cap on retries as a share of recent calls (10% per second in SIM-2) |
| **Mock / MockServer** | Local HTTP server speaking both providers' wire formats; the `X-Casebook-Pack` header selects a fault |
| **Mock clock** | The `X-Casebook-Date` header, which sets "today" for date-based rules such as model retirements |
| **Dossier** | A fictional named account (`cases/dossiers/*.yaml`): contacts, dependencies, models in use, code paths |
| **Exposure** | A finding from `exposure.scan`: something in an account's code that breaks, or will on a date |
| **P1 / P2 / P3** | Exposure severity: P1 broken now or within 14 days, P2 within 60 days or certain on upgrade, P3 hygiene. Also used for incident update severity |
| **Gate** | A hard pass/fail threshold in the triage eval: `false_retryable == 0` and `redaction_leaks == 0` |
| **Strict / safe accuracy** | Strict: exact class match. Safe: exact match, or "needs more info" where abstaining is acceptable. "Errors = failures" counts LLM errors as wrong instead of as safe abstains |
| **Stand-in IdP** | `casebook/identity/idp.py`: issues real signed SAML assertions; not a product IdP |
| **SCIM / JIT** | User provisioning from an identity provider (SCIM) or at first login (just-in-time) |
| **WIF** | Workload Identity Federation: exchanging an external OIDC token (e.g. from CI) for an API access token |
| **MCP** | Model Context Protocol: how agents call external tools and data servers |
| **Live capture** | A real API response recorded by `python -m casebook live`, redacted and diffed against `expected.json` |
| **Request id** | Provider-issued id for one HTTP request (`x-request-id` for OpenAI, `request-id` for Anthropic); the first thing support asks for |
| **Client request id** | `X-Client-Request-Id`, set by the caller; the only way to trace a call that never got a response |
