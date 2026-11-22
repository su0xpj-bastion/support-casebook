# Code reference

The public functions on the main paths, with their real callers and the tests that cover them. Line links point at the definition. Callers were found by searching the source, not inferred from names.

Per-pack scenario functions (`ag01`…`ag28`, `id01`…`oa41`, the MCP and CLI lab scenarios) follow one shape and are grouped at the end.

## Evidence core

### `packs`: [casebook/packs.py](../casebook/packs.py)

| Function | Purpose | Called by | Inputs → output | Side effects / errors | Tests |
|---|---|---|---|---|---|
| [`load_all`](../casebook/packs.py#L35) | Load every `faults/*/*/pack.yaml` and its `expected.json` | `get`, `cli`, `triage.dataset`, `gen.runbooks`, `tools/*`, tests | none → `dict[id, Pack]` | Reads files once per process (`lru_cache`); a malformed YAML/JSON raises | `test_pack_metadata_complete` |
| [`get`](../casebook/packs.py#L46) | One pack by id | `repro`, `mockserver`, `live`, `scenarios`, sims | `pack_id` → `Pack` | `KeyError` for an unknown id | every pack test |
| [`Pack.provenance`](../casebook/packs.py#L28) | Weakest tag across the expected fields (`synthesized` < `doc-partial` < `doc-verbatim` < `live-captured`) | `repro`, `packs` CLI, `gen_test_matrix` | → `str` | none | `test_pack_provenance_tagged_per_field` |

### `redact`: [casebook/redact.py](../casebook/redact.py)

| Function | Purpose | Called by | Inputs → output | Side effects / errors | Tests |
|---|---|---|---|---|---|
| [`redact`](../casebook/redact.py#L36) | Replace secrets with `[REDACTED:<kind>]`. Patterns run most specific first; key bodies may be wrapped across lines or contain a literal `\n` | `packet`, `live`, `cli`, `gen.updates.lint_update`, `triage.classify` | `str` → `(str, Counter[kind])` | none | `test_red_01`…`test_red_03` |
| [`redact_headers`](../casebook/redact.py#L48) | Drop values of secret headers (`authorization`, `x-api-key`, cookies…); decode base64 `x-error-json` and drop it if it hides a secret; redact the rest | `packet.from_http`, `live.capture` | `dict` → `(dict, count)` | Bad base64 is treated as empty, not an error | `test_red_04`, `test_red_05` |

### `packet`: [casebook/packet.py](../casebook/packet.py)

| Function | Purpose | Called by | Inputs → output | Side effects / errors | Tests |
|---|---|---|---|---|---|
| [`from_http`](../casebook/packet.py#L90) | Build a Support Packet from a status, headers and body: provider request id, org/project, client request id, rate headers, error fields, redaction count, warnings | `from_sdk_error`, `from_client_error`, `scenarios._oa15`, tests | provider, status, headers, body, optional request → `dict` | Treats an HTML body as "no API envelope"; warns when body and header request ids differ | `test_pkt_01`…`test_pkt_06` |
| [`from_sdk_error`](../casebook/packet.py#L204) | Packet from an `APIStatusError`. Reads the raw response because `exc.body` differs between the two SDKs | `repro.run`, `policy._decide`, `live.capture`, `sims.s1_outage` | provider, exc → `dict` | Falls back to `exc.body` when the response isn't JSON | `test_packet_valid` (every error pack) |
| [`from_client_error`](../casebook/packet.py#L186) | Packet for failures with no HTTP response (timeout, connection reset) | `repro.run`, `sims.s1_outage` | provider, exc, kind → `dict` | Warns when no `X-Client-Request-Id` was sent | `test_oa14_timeout_packet_carries_client_request_id` |
| [`from_text`](../casebook/packet.py#L239) | Packet from free ticket text: redact, then extract statuses, codes and `req_` ids | `cli._cmd_redact`, `triage.classify` | text → `dict` (adds `text`, `mentioned`) | none | `test_pkt_07`, `test_pkt_07b`, `test_bare_429_asks_for_more_info` |
| [`parse_retry_after`](../casebook/packet.py#L46) | Seconds from `retry-after-ms` (preferred) or `retry-after` | `from_http` | headers → `float \| None` | HTTP-date form returns `None` | `test_pkt_08_retry_after_ms_wins` |
| [`validate_client_request_id`](../casebook/packet.py#L63) | OpenAI's `X-Client-Request-Id` limits (ASCII, ≤512 chars) | `from_http` | str → `list[str]` problems | none | `test_pkt_04_client_request_id_rules` |

### `rules`: [casebook/rules.py](../casebook/rules.py)

| Function | Purpose | Called by | Inputs → output | Tests |
|---|---|---|---|---|
| [`classify`](../casebook/rules.py#L33) | Packet → `Verdict`. Routes to client-side, OpenAI or Anthropic rules | `policy._decide`, `repro.run`, `live.capture`, `cli`, `triage.classify` | packet → `Verdict(cls, retryable, owner, basis, confidence, doc)` | `test_rules_verdict_matches_pack`, `test_rules.py` |
| [`_openai`](../casebook/rules.py#L62) | 429 split by `error.code` (billing codes N, `slow_down` Y), flex tier, 503 overloaded, 401/403/400 by message where no code exists | `classify` | status, error, request meta → `Verdict` | `test_openai_429_family` |
| [`_anthropic`](../casebook/rules.py#L99) | 429 split by `details.error_code` and presence of `retry-after`; workspace limit by header comparison; 400 self-set limit by message prefix; 529/overloaded; 504; edge 413 | `classify` | status, error, retry_after, rate headers → `Verdict` | `test_anthropic_look_alikes` |

`confidence` tells the reader how fragile a verdict is: `code` (documented field), `message-prefix`, `header-heuristic`, `status-only`, `client-side`. `test_message_matched_rules_are_flagged_fragile` keeps message-based verdicts labelled.

### `policy`: [casebook/policy.py](../casebook/policy.py)

| Function | Purpose | Called by | Inputs → output | Side effects / errors | Tests |
|---|---|---|---|---|---|
| [`call_openai`](../casebook/policy.py#L29) / [`call_anthropic`](../casebook/policy.py#L54) | Up to `MAX_ATTEMPTS` (3) calls with SDK retries off; retry only on a `Y` verdict | `repro.run` | client, `trace`, `sleep`, create kwargs → SDK response | Sleeps between attempts; re-raises the original exception with a bare `raise` | `test_request_counts_python_sdk_default_vs_policy` |
| [`_decide`](../casebook/policy.py#L79) | Build packet, classify, return the delay or `None` | `call_*` | provider, exc, attempt, trace → `float \| None` | Appends to `trace` | same |
| [`backoff`](../casebook/policy.py#L24) | `max(retry_after, 2**attempt + random())` | `_decide` | attempt, retry_after → seconds | none | `test_policy_backoff_honours_retry_after` |

### `checks`: [casebook/checks.py](../casebook/checks.py)

HTTP-200 anomaly detectors. Each returns a `Finding` (cls, detail, pack) or `None`. Called by `scenarios.py` and `agent_scenarios.py`; tested in `tests/test_anomalies.py` and `tests/test_agents.py`.

| Function | Detects |
|---|---|
| [`service_tier`](../casebook/checks.py#L23) | Requested `priority`, served `default` (OA-13) |
| [`org_mismatch`](../casebook/checks.py#L32) | Billed to a different org than expected (OA-15) |
| [`cache`](../casebook/checks.py#L41) / [`first_prefix_change`](../casebook/checks.py#L59) | Prompt cache below minimum or invalidated by prefix drift, and where the prefix changed (AN-06, AN-07) |
| [`stream_usage`](../casebook/checks.py#L71) | `include_usage` requested but no usage chunk received (OA-17) |
| [`stop_reason`](../casebook/checks.py#L87) | Stop reasons that look like outages but are HTTP 200 by design: refusal, context window full, max tokens, length, content filter (AN-17, AN-23, OA-21) |
| [`tool_result_order`](../casebook/checks.py#L104) | Text placed before or after `tool_result` blocks (AN-22, AG-01) |
| [`strict_schema_problems`](../casebook/checks.py#L122) | Schema not valid for strict mode (OA-22, AG-06) |
| [`batch_reconcile`](../casebook/checks.py#L141) | Batch results out of order, errored or expired (AN-24) |
| [`stateless_replay`](../casebook/checks.py#L157) | Reasoning items dropped when replaying a stateless conversation (AG-08) |
| [`connector_config_problems`](../casebook/checks.py#L170) | MCP connector config that can't work (AG-15) |

### `models`: [casebook/models.py](../casebook/models.py)

Lifecycle and capability facts as data: `Retirement` records, [`retirement_for`](../casebook/models.py#L71), [`is_retired`](../casebook/models.py#L78), [`no_prefill`](../casebook/models.py#L83), [`no_thinking_enabled`](../casebook/models.py#L87). Used by `mockserver` (to fail requests on and after a date) and `exposure`. Tested in `tests/test_migrations.py` and `tests/test_exposure.py`.

## Runners

### `repro`: [casebook/repro.py](../casebook/repro.py)

| Function | Purpose | Called by | Inputs → output | Tests |
|---|---|---|---|---|
| [`make_client`](../casebook/repro.py#L28) | OpenAI or Anthropic SDK client pointed at the mock, tagged with `X-Casebook-Pack` | `repro.run`, `scenarios`, `agent_*scenarios`, sims | provider, base_url, pack_id, max_retries, timeout, headers → client | all pack tests |
| [`request_kwargs`](../casebook/repro.py#L41) | Default request for the provider, merged with the pack's overrides | same, plus `live` | provider, overrides → kwargs | all pack tests |
| [`run`](../casebook/repro.py#L55) | Run one error pack in `sdk-default` or `policy` mode; collect requests sent, request ids, exception, packet, verdict | `cli._cmd_repro`, `tests/test_packs.py` | pack_id, srv, mode, sleep → `dict` | `test_packs.py` (8 parametrized tests per error pack) |

### `mockserver`: [casebook/mockserver.py](../casebook/mockserver.py)

| Item | Purpose |
|---|---|
| [`MockServer`](../casebook/mockserver.py#L797) | Context manager: starts `ThreadingHTTPServer` on 127.0.0.1 and a free port in a daemon thread; optional TLS (`tls=(cert, key)`); `.url`, `.state` |
| [`State`](../casebook/mockserver.py#L81) | Thread-safe request log and per-pack hit counter (`hits`), reset per run |
| [`Handler.do_POST`](../casebook/mockserver.py#L166) | Routes by path (query string ignored); reads `X-Casebook-Pack`; serves the pack's `mode` |
| [`Handler.do_GET`](../casebook/mockserver.py#L133) | Model listing, batch status, Responses resume, and the debug endpoints `/_casebook/hits` and `/_casebook/requests` |
| [`Handler._anthropic_validate`](../casebook/mockserver.py#L357) | Documented request-level failures by model and API version: retired model (synthesized 404), context window, forced `tool_choice`, prefill, `thinking.type: enabled`, sampling params, computer-tool version, edited thinking signatures. Dates come from the `X-Casebook-Date` mock clock |

### `scenarios.run`: [casebook/scenarios.py](../casebook/scenarios.py#L26)

Dispatches every non-error pack id to its function: anomaly packs in `scenarios.py`, then the `SCENARIOS` dicts of `agent_scenarios`, `agent_sdk_scenarios`, `mcp_lab.scenarios`, `client_lab.scenarios`, `identity.scenarios`, and the three sims. Resets the mock's state, runs the function, and adds `pack` and `requests_sent`.

### `ts_repro`: [casebook/ts_repro.py](../casebook/ts_repro.py)

[`available`](../casebook/ts_repro.py#L16) checks for Node and `ts/node_modules`. [`run`](../casebook/ts_repro.py#L21) runs `node ts/repro.mjs <provider> <url> <pack> <request> <timeout> <client-request-id>`, parses the last stdout line as JSON, and adds the mock's request count. Raises `RuntimeError` if Node exits non-zero. Tests: `tests/test_ts_sdks.py` (skipped without Node).

## Outputs

### `gen.updates`: [casebook/gen/updates.py](../casebook/gen/updates.py)

| Function | Output | Called by | Tests |
|---|---|---|---|
| [`customer_update`](../casebook/gen/updates.py#L25) | Markdown with the six `UPDATE_SECTIONS`; retry advice only when `retryable` allows it | `sims.s1_outage`, `sims.artifacts` | `test_gen_01` |
| [`escalation_brief`](../casebook/gen/updates.py#L45) | Internal brief: request ids, time window, blast radius, repro command, ask | `sims.artifacts` | `test_gen_02` |
| [`rca`](../casebook/gen/updates.py#L64) | Five-part RCA (`RCA_SECTIONS`) | `sims.artifacts` | `test_gen_03` |
| [`lint_update`](../casebook/gen/updates.py#L76) | `list[str]` problems; empty = pass | `sims.s1_outage`, `sims.artifacts` | `test_lint_01`…`test_lint_05` |

### `gen.runbooks`: [casebook/gen/runbooks.py](../casebook/gen/runbooks.py)

[`render`](../casebook/gen/runbooks.py#L25) turns one `Pack` into Markdown (confirm, fix from `fix.md`, customer guidance, when to escalate, live findings, provenance). [`generate`](../casebook/gen/runbooks.py#L46) writes all of them to `runbooks/`. Called by `python -m casebook runbooks`. No dedicated test; the output is committed and reviewed.

### `exposure`: [casebook/exposure.py](../casebook/exposure.py)

| Function | Purpose | Called by | Tests |
|---|---|---|---|
| [`scan`](../casebook/exposure.py#L45) | Dossier + code → sorted findings | `cli._cmd_exposure`, tests | `test_exp_01`…`test_exp_05` |
| [`_line_rules`](../casebook/exposure.py#L78), [`_file_rules`](../casebook/exposure.py#L123), [`_dossier_rules`](../casebook/exposure.py#L139) | The three rule layers (see [workflow 4](workflows.md#4-proactive-exposure-scan)) | `scan` | same |
| [`draft_note`](../casebook/exposure.py#L165) | Markdown note with a customer-ready paragraph for P1 items | `cli._cmd_exposure` | `test_exp_06` |

### `live`: [casebook/live.py](../casebook/live.py)

[`capture`](../casebook/live.py#L54) sends the real request ([`_call`](../casebook/live.py#L35), retries off), redacts, classifies, diffs ([`diff`](../casebook/live.py#L91)) and writes the capture file. Raises `PermissionError` for a keyed pack without `keyed=True`; needs `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` for keyed packs. Only `cli._cmd_live` calls it. Tests check committed captures offline (`tests/test_live_captures.py`).

### `triage`: [casebook/triage/](../casebook/triage/)

| Function | Purpose | Tests |
|---|---|---|
| [`dataset.synthetic`](../casebook/triage/dataset.py#L96) / [`adversarial`](../casebook/triage/dataset.py#L121) / [`split`](../casebook/triage/dataset.py#L129) | 240 seeded tickets from the packs (full, partial, vague evidence; planted keys) + 20 hand-written; split by error family | `test_dataset_shape`, `test_split_is_by_family` |
| [`classify.triage_rules`](../casebook/triage/classify.py#L81) | Rules-only triage | `test_rules_gates_on_every_ticket`, `test_specific_message_beats_generic_type` |
| [`classify.triage_llm`](../casebook/triage/classify.py#L113) | One Chat Completions call in JSON mode; errors labelled, not hidden | `test_llm_errors_are_labelled_not_hidden`, `test_secrets_never_reach_the_model` |
| [`classify.triage_combined`](../casebook/triage/classify.py#L132) | Rules first, then the guarded LLM | `test_guard_blocks_retryable_guess_without_code`, `test_guard_rejects_invented_quotes` |
| [`evaluate.metrics`](../casebook/triage/evaluate.py#L45) | Strict/safe accuracy, macro-F1, false-retryable, wrong answers, unsupported quotes, leaks, LLM errors | `test_committed_report_gates` |
| [`evaluate.run`](../casebook/triage/evaluate.py#L69) / [`main`](../casebook/triage/evaluate.py#L102) | Run all three modes, compute gates, write results and predictions | run by hand (needs Ollama) |
| [`evaluate.rescore`](../casebook/triage/evaluate.py#L159) | Recompute metrics from a saved predictions file without calling the model | run by hand |

## Labs (grouped)

All scenario functions take the mock server (or nothing) and return a `dict` with a `finding` plus the evidence behind it. `scenarios.run` dispatches them; each has a test named after its pack.

| Module | Functions | What they drive |
|---|---|---|
| [`agent_scenarios`](../casebook/agent_scenarios.py) | `ag01`…`ag15` | Plain SDK tool-use, server-tool, Responses and MCP-connector flows against the mock |
| [`agent_sdk_scenarios`](../casebook/agent_sdk_scenarios.py) | `ag22`…`ag28` | Real `claude-agent-sdk` and `openai-agents` runs |
| [`mcp_lab/scenarios`](../casebook/mcp_lab/scenarios.py) | AG-16…AG-21 | Real Python and TS MCP servers/clients through [`proxy`](../casebook/mcp_lab/proxy.py) and an OAuth stand-in ([`auth`](../casebook/mcp_lab/auth.py)) |
| [`client_lab/scenarios`](../casebook/client_lab/scenarios.py) | AN-44…56, OA-43, OA-48 | Real CLIs via [`claude_cli.run`](../casebook/client_lab/claude_cli.py#L23) and [`codex_cli.run`](../casebook/client_lab/codex_cli.py#L23); TLS chain from [`tls.make_chain`](../casebook/client_lab/tls.py#L15) |
| [`identity/scenarios`](../casebook/identity/scenarios.py) | `id01`…`id07`, `an32`…`an38`, `oa30`…`oa41` | [`StandInIdP`](../casebook/identity/idp.py#L26), the SP ([`identity/sp.py`](../casebook/identity/sp.py)), [`ScimStore`](../casebook/identity/scim.py#L13), [`AuthorizationServer`](../casebook/identity/oauth.py#L34), [`WifProvider`](../casebook/identity/wif.py#L52) |
| [`sims`](../casebook/sims/) | `s1_outage.run`, `s2_retry_storm.run`, `s3_silent_degradation.run`, `artifacts.run_all` | See [workflow 5](workflows.md#5-incident-simulation-and-generated-updates) |

## CI tooling

Detailed in [ci.md](ci.md); tests in `tests/test_ci.py`.

| Function | Purpose | Called by |
|---|---|---|
| [`select.import_graph`](../casebook/ci/select.py) / `dependents` / `select` | Changed files → test files and node ids, with a reason for each | `ci select`, `runner.targets`, `tools/ci_experiments.py` |
| [`quarantine.analyse`](../casebook/ci/quarantine.py) / `update` | Flaky = passed and failed on one commit; quarantine and release policy | `ci quarantine update`, tests |
| [`pytest_plugin`](../casebook/ci/pytest_plugin.py) | History rows per test; quarantined failures reported as xfail | registered in `tests/conftest.py` |
| [`shard.plan`](../casebook/ci/shard.py) / `file_durations` | Longest-first file assignment to N shards | `ci shard`, `runner.targets`, `ci-shards.yml` |
| [`signatures.classify`](../casebook/ci/signatures.py) | Failure text → signature (class, retry, owner, fix) | `runner.run`, `ci classify` |
| [`MergeQueue.process`](../casebook/ci/mergequeue.py) | Batched speculative merges with bisection and compare-and-swap | tests, `tools/ci_experiments.py queue` |
| [`runner.run`](../casebook/ci/runner.py) | Select/shard → pytest → rerun failures → classify → exit code | `ci test`, `ci.yml` |

## Tools

| Script | Purpose |
|---|---|
| [`tools/doc_links.py`](../tools/doc_links.py) | List every doc URL cited by a pack; `--check` fetches each and reports non-200s. Run weekly by `doc-drift.yml` |
| [`tools/gen_test_matrix.py`](../tools/gen_test_matrix.py) | Regenerate [test-matrix.md](test-matrix.md) from the packs and pytest's collection |
| [`tools/ci_experiments.py`](../tools/ci_experiments.py) | The experiments behind [ci.md](ci.md): selection replay, mutation, repeated runs, merge queue, Actions timings |
| [`tools/check_docs.py`](../tools/check_docs.py) | Check that every link in the docs resolves and every `#L<n>` link lands on the named definition |
