# Runbooks

| ID | Title | Retry | Provenance |
|---|---|---|---|
| [AG-01](AG-01.md) | tool_use without an immediately following tool_result (or text first) -> 400 | - | synthesized |
| [AG-02](AG-02.md) | Mixed server/client turn: text added, or server tool dropped from tools -> 400 | - | doc-partial |
| [AG-03](AG-03.md) | pause_turn continued without the container id: earlier files silently gone | - | synthesized |
| [AG-04](AG-04.md) | Web search/fetch config 400: ZDR without allowed_callers; allowed+blocked domains | - | synthesized |
| [AG-05](AG-05.md) | Failing tool + no retry cap: the agent loop never ends | - | synthesized |
| [AG-06](AG-06.md) | strict:true sent, schema not strict-compatible: echoed strict:false, no error | - | doc-verbatim |
| [AG-07](AG-07.md) | Duplicate parallel tool calls (gpt-4.1-nano-2025-04-14) | - | doc-verbatim |
| [AG-08](AG-08.md) | Tool use degrades after stateless replay: reasoning items dropped | - | doc-verbatim |
| [AG-09](AG-09.md) | Bill grows every turn with previous_response_id | - | synthesized |
| [AG-10](AG-10.md) | Remote MCP works on turn 1, 401 on turn 2: authorization not stored | - | synthesized |
| [AG-11](AG-11.md) | MCP call 'stuck': waiting for approval | - | doc-verbatim |
| [AG-12](AG-12.md) | connector_id rejected on models released after 2026-09-01 | - | synthesized |
| [AG-13](AG-13.md) | MCP connector: undefined server 400; typo in configs is a silent no-op | - | synthesized |
| [AG-14](AG-14.md) | Local/stdio MCP server can't attach to the API connector | - | synthesized |
| [AG-15](AG-15.md) | claude.ai custom connector fails: private host, non-443 port, missing OAuth callback | - | doc-verbatim |
| [AG-16](AG-16.md) | MCP revision mismatch: 2026-07-28 client vs 2025-11-25 server | - | synthesized |
| [AG-17](AG-17.md) | Gateway drops Mcp-Method/Mcp-Name or rewrites MCP-Protocol-Version | - | doc-verbatim |
| [AG-18](AG-18.md) | Progress updates all arrive at the end: SSE buffered by a proxy | - | synthesized |
| [AG-19](AG-19.md) | 403 insufficient_scope vs 401 wrong audience | - | synthesized |
| [AG-20](AG-20.md) | DCR-only client vs CIMD-only authorization server | - | synthesized |
| [AG-21](AG-21.md) | One malformed tool definition hides every tool | - | doc-verbatim |
| [AG-22](AG-22.md) | Headless Agent SDK run denies the tool — and still reports success | - | doc-verbatim |
| [AG-23](AG-23.md) | can_use_tool never fires: an allow rule approves the tool first | - | doc-verbatim |
| [AG-24](AG-24.md) | allowed_tools didn't restrict: unlisted Bash runs under bypassPermissions | - | doc-verbatim |
| [AG-26](AG-26.md) | Agents SDK run aborts: MaxTurnsExceeded; input guardrail tripwire | - | live-captured |
| [AG-27](AG-27.md) | Session + previous_response_id: mutually exclusive state options | - | live-captured |
| [AG-28](AG-28.md) | Side effect happened before a timeout; replaying the turn repeats it | - | synthesized |
| [AN-01](AN-01.md) | Monthly tier spend cap — 429 with no retry-after; retries are useless | N | doc-partial |
| [AN-02](AN-02.md) | Self-set workspace spend limit returns 400, not 429 | N | doc-partial |
| [AN-03](AN-03.md) | 429 ramp_limit_exceeded while under ITPM/OTPM limits | Y | synthesized |
| [AN-04](AN-04.md) | 429 on a sub-minute burst under the per-minute limit | Y | synthesized |
| [AN-05](AN-05.md) | One workspace starves: workspace token limit is the binding one | Y | synthesized |
| [AN-06](AN-06.md) | cache_control set, cache never used: prefix below the minimum | - | doc-verbatim |
| [AN-07](AN-07.md) | Caching broke after deploy: tool order changed | - | doc-verbatim |
| [AN-08](AN-08.md) | 529 overloaded_error during peak | Y | doc-verbatim |
| [AN-09](AN-09.md) | Stream dies after HTTP 200 with event: error (overloaded) | Y | doc-verbatim |
| [AN-10](AN-10.md) | Long non-streaming call fails instantly, no request-id | N | doc-verbatim |
| [AN-11](AN-11.md) | 504 timeout_error; SDK retries create several request-ids per logical call | C | synthesized |
| [AN-12](AN-12.md) | Browser app gets 401 with a valid key (CORS blocked for the org) | N | synthesized |
| [AN-13](AN-13.md) | 401 after key rotation or offboarding | N | doc-verbatim |
| [AN-14](AN-14.md) | All prod keys die at once: workspace archived (same 401 as AN-13) | N | synthesized |
| [AN-15](AN-15.md) | 402 billing_error | N | doc-partial |
| [AN-16](AN-16.md) | 413 from the edge: HTML body, no request-id | N | synthesized |
| [AN-17](AN-17.md) | Context: 400 'prompt is too long' vs 200 model_context_window_exceeded | N | synthesized |
| [AN-18](AN-18.md) | Migration 400s on newest models: prefill, thinking.enabled, sampling params, old computer tool | N | synthesized |
| [AN-19](AN-19.md) | Forced tool_choice works on Sonnet 4.5, 400 on Sonnet 5.5 | N | doc-verbatim |
| [AN-20](AN-20.md) | Thinking-signature 400 after editing earlier history | N | synthesized |
| [AN-21](AN-21.md) | SDK major upgrade: temperature= is a TypeError in the SDK, and a 400 under anthropic-version 2026-10-21 | N | synthesized |
| [AN-22](AN-22.md) | Near-empty reply after a tool result (text appended after tool_result) | N | synthesized |
| [AN-23](AN-23.md) | Refusal reported as an outage (HTTP 200, stop_reason=refusal) | N | synthesized |
| [AN-24](AN-24.md) | Batch results 'lost' or mismatched: out of order, errored, expired | C | synthesized |
| [AN-26](AN-26.md) | claude-sonnet-4-5 calls fail from 2026-11-30 | N | synthesized |
| [AN-44](AN-44.md) | Both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN set: both headers go out | - | live-captured |
| [AN-45](AN-45.md) | Managed forceLoginOrgUUID blocks an env API key at startup | - | synthesized |
| [AN-50](AN-50.md) | Corporate TLS inspection: SELF_SIGNED_CERT_IN_CHAIN; NODE_EXTRA_CA_CERTS fixes it | - | synthesized |
| [AN-51](AN-51.md) | SOCKS proxy: Claude Code hangs instead of erroring | - | doc-verbatim |
| [AN-55](AN-55.md) | apiKeyHelper fails: request still sent with an empty bearer token | - | doc-verbatim |
| [AN-56](AN-56.md) | OTel exporter variables in project settings are ignored | - | doc-verbatim |
| [OA-43](OA-43.md) | forced_login_method=chatgpt with a stored API key: Codex logs out and exits | - | doc-verbatim |
| [OA-48](OA-48.md) | Codex behind TLS inspection: 'Reconnecting... waiting for network' forever | - | synthesized |
| [AN-32](AN-32.md) | Domain stuck Pending: TXT on www, wrong case, or re-added domain | - | synthesized |
| [AN-33](AN-33.md) | IdP cert expired with Require SSO on: whole org locked out | - | synthesized |
| [AN-35](AN-35.md) | Require SSO for Claude: personal accounts on the domain become unreachable | - | synthesized |
| [AN-36](AN-36.md) | SCIM saved before assigning everyone: mass deprovisioning | - | synthesized |
| [AN-37](AN-37.md) | Group mappings downgrade the admin who configured them | - | synthesized |
| [AN-38](AN-38.md) | JIT never auto-removes: user reappears, or keeps a seat | - | synthesized |
| [ID-01](ID-01.md) | SAML NameID is a username or alias, not the primary email | - | synthesized |
| [ID-02](ID-02.md) | SAML assertion not yet valid / expired: IdP clock drift or a stale assertion | - | synthesized |
| [ID-03](ID-03.md) | SAML Audience / ACS copied from another org's app | - | synthesized |
| [ID-04](ID-04.md) | IdP rotated its signing certificate; SP still trusts the old one | - | synthesized |
| [ID-05](ID-05.md) | OAuth redirect_uri mismatch (trailing slash, http vs https) | - | synthesized |
| [ID-06](ID-06.md) | OAuth invalid_grant: revoked refresh token, reused code | - | synthesized |
| [ID-07](ID-07.md) | OIDC: key rotated and old key removed at once; strict issuer/audience | - | synthesized |
| [OA-30](OA-30.md) | ChatGPT SCIM: user removed in the workspace comes back | - | synthesized |
| [OA-38](OA-38.md) | WIF invalid_grant after a second mapping also matches | - | synthesized |
| [OA-39](OA-39.md) | WIF fails after key rotation (uploaded JWKS stale / old key removed early) | - | synthesized |
| [OA-40](OA-40.md) | WIF mapping wildcards rejected | - | synthesized |
| [OA-41](OA-41.md) | WIF clients fail at about 1 h: no refresh token | - | synthesized |
| [OA-01](OA-01.md) | 429 rate limit under normal load | Y | synthesized |
| [OA-02](OA-02.md) | 429 slow_down: ramped too fast, dashboard shows headroom | Y | synthesized |
| [OA-04](OA-04.md) | Prepaid credit balance exhausted | N | synthesized |
| [OA-05](OA-05.md) | Project monthly spend limit reached | N | synthesized |
| [OA-06](OA-06.md) | Organization approved usage limit reached | N | synthesized |
| [OA-07](OA-07.md) | 401 Incorrect API key provided | N | doc-partial |
| [OA-08](OA-08.md) | 403 country, region, or territory not supported | N | synthesized |
| [OA-10](OA-10.md) | 503 server_is_overloaded surfaces as InternalServerError, not RateLimitError | Y | synthesized |
| [OA-11](OA-11.md) | 400 Invalid service_tier after an admin restricts tiers (even with auto) | N | synthesized |
| [OA-12](OA-12.md) | Flex 429 Resource Unavailable read as a rate limit | Y | synthesized |
| [OA-13](OA-13.md) | Requested priority, served and billed as default | - | doc-verbatim |
| [OA-14](OA-14.md) | Client timeout with no x-request-id — and SDK retries resend the work | C | synthesized |
| [OA-15](OA-15.md) | Usage billed to the wrong organization | - | synthesized |
| [OA-16](OA-16.md) | Long call killed at exactly 10 minutes (SDK default timeout) | C | doc-verbatim |
| [OA-17](OA-17.md) | Stream crashes on chunk.choices[0]; usage missing after interruption | - | doc-verbatim |
| [OA-18](OA-18.md) | Responses stream returns 200, then response.failed | C | synthesized |
| [OA-19](OA-19.md) | Background stream dropped: resume with starting_after | Y | synthesized |
| [OA-21](OA-21.md) | Structured output: refusal parses to None; length raises before parsing | N | synthesized |
| [OA-22](OA-22.md) | 'strict is on' but the schema is not strict-compatible | N | doc-verbatim |
| [OA-24](OA-24.md) | gpt-4o / gpt-4.1 calls fail from 2027-01-14 | N | synthesized |
| [SIM-1](SIM-1.md) | Provider outage — classify 'platform-side' from client signals before the status page | - | synthesized |
| [SIM-2](SIM-2.md) | Retry storm — the same demand, three client retry strategies, one capacity incident | - | synthesized |
| [SIM-3](SIM-3.md) | Silent quality degradation on a sticky slice — canary evals vs per-session probes | - | synthesized |
