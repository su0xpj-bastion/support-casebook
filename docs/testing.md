# Testing strategy

## What the tests prove

Each pack states an expected outcome: verdict, retry Y/N/C, number of requests sent, exception class, or a finding. The tests run the pack through real client code and assert that outcome. A test passing means the behaviour described in the pack was reproduced on this machine. It doesn't mean the real provider API behaves that way, unless the pack has a `live/` capture.

Test status is recorded by CI ([`ci.yml`](../.github/workflows/ci.yml)) on every push, not in this file. Check the latest run on the repository's Actions tab.

## Layers

| Layer | What runs | Files | Collected cases |
|---|---|---|---|
| Unit | Pure functions: redaction patterns, packet parsing, rules on hand-built packets, update linter, exposure rules | `test_redact.py`, `test_packet.py`, `test_rules.py`, `test_gen.py`, `test_exposure.py` | 50 |
| Pack contract | Every pack's files are complete and every expected field is provenance-tagged | `test_packs.py` (`test_pack_metadata_complete`, `test_pack_provenance_tagged_per_field`) | 202 |
| Integration: Python SDKs | Every error pack through `openai` / `anthropic`, SDK defaults vs policy, against the mock | `test_packs.py` (remaining tests) | 179 |
| Integration: Node SDKs | Same error packs through the official Node SDKs | `test_ts_sdks.py` | 51 |
| Integration: HTTP-200 anomalies and migrations | Real SDK calls whose failure is in the response content or appears on a date | `test_anomalies.py`, `test_migrations.py` | 25 |
| End to end: agents and tools | Real `claude-agent-sdk` (with bundled Claude Code) and `openai-agents` runs | `test_agents.py` | 21 |
| End to end: MCP | Real Python and TS MCP servers and clients, through a misbehaving proxy and an OAuth stand-in | `test_mcp_interop.py` | 6 |
| End to end: CLIs | Real Claude Code and Codex binaries with isolated config homes, TLS inspection CA, OTLP collector | `test_clients.py` | 8 |
| Identity | Real XML signature and JWT verification against modelled policy | `test_identity.py` | 40 |
| Concurrency / failure injection | Incident sims with 10–20 concurrent SDK clients | `test_sims.py` | 4 |
| Triage | Dataset shape, family split, rules gates, LLM guard, committed report gates | `test_triage.py` | 11 |
| Evidence files | Committed live captures are well-formed and contain no secret | `test_live_captures.py` | 5 |
| CI tooling | Selection (incl. a real-import check per test file), quarantine, history plugin, sharding, signatures against reproduced failures, merge queue on real git, reruns | `test_ci.py`, `test_ci_graph.py` | 51 |
| SQL investigation | Saved queries reproduce the published triage numbers and repro results | `test_sql.py` | 7 |
| | | **Total** | **660** |

The per-pack listing is generated: [test-matrix.md](test-matrix.md). Every one of the 101 packs has at least one behavioural test.

CI also runs `tools/check_docs.py` (every doc link resolves and every line link lands on the named definition) and fails if `docs/test-matrix.md` is out of date.

## Coverage by concern

| Concern | Where it's tested |
|---|---|
| Negative cases and look-alikes | `test_anthropic_look_alikes`, `test_openai_429_family`, `test_an13_and_an14_are_indistinguishable_at_the_api`, `test_red_03_lookalikes_are_not_redacted` |
| Retry and timeout behaviour | `test_request_counts_python_sdk_default_vs_policy`, `test_policy_backoff_honours_retry_after`, `test_policy_trace_records_every_failed_attempt`, `test_an11_sdk_retries_mint_three_request_ids_for_one_logical_call`, `test_oa14_timeout_packet_carries_client_request_id`, `test_oa16_sdk_default_timeouts_are_ten_minutes` |
| Boundary conditions | `test_exp_05_severity_escalates_as_the_date_approaches`, `test_an26_retirement_flips_on_the_date`, `test_oa24_shutdown_flips_on_the_date`, `test_id02_clock_skew`, `test_pkt_04_client_request_id_rules` |
| Authentication and authorization | `test_id01`…`test_id07`, `test_ag19_scope_vs_audience`, `test_ag20_dcr_vs_cimd`, `test_an44_both_credentials_sent`, `test_an45_forced_org_blocks_env_key`, `test_an55_api_key_helper`, `test_oa43_forced_login_method` |
| Data validation | `test_packet_valid` (JSON Schema), `test_oa22_strict_lint`, `test_ag06_strict_silently_downgraded`, `test_ag21_one_bad_tool_hides_all_tools` |
| Secret handling | `test_red_01`…`test_red_05`, `test_pkt_06_message_secrets_redacted_and_counted`, `test_secrets_never_reach_the_model`, `test_lint_02_secret`, `test_capture_is_well_formed_and_leaks_nothing` |
| Concurrency | `test_sim1_…`, `test_sim2_amplification_ordering`, `test_sim3_…` |
| Idempotency | `test_ag28_replay_repeats_side_effect_unless_deduped_on_business_key` |
| Regression of past bugs | `test_llm_errors_are_labelled_not_hidden` (errors once scored as safe), `test_signxml_reports_expiry_as_invalid_signature`, `test_ag18_buffering_only_when_header_lost` |

## Not tested

- **Real provider APIs.** Only three packs (OA-07, AN-13, AN-16) have live captures, all with a fake key. Everything else runs against the mock.
- **Real enterprise IdPs** (Okta, Entra, Keycloak) and real Claude or ChatGPT enterprise tenants.
- **Managed settings at system paths, MDM, Windows** for the CLI packs (they need sudo or another OS).
- **Runbook generation** (`gen.runbooks`) has no dedicated test; its output is committed.
- **The LLM triage itself** isn't run in CI (needs Ollama). CI checks the committed results against the gates.
- **Performance.** There are no timing assertions apart from the measured latencies in AG-18 and the sims.

## Recommended next tests

- Keyed live captures for AN-02, OA-05 and OA-11 (steps in each `pack.yaml` under `live_setup`), and AN-26 after its retirement date.
- A test that `runbooks/` is up to date with `generate()` output.

## Running the tests

Prerequisites: Python 3.14, `uv` (or pip), Node 22 for the Node SDK, MCP and Codex tests.

```bash
uv venv -p python3.14 .venv && uv pip install -p .venv -r requirements.txt
(cd ts && npm ci)                       # optional: without it the Node, TS-MCP and Codex tests are skipped
.venv/bin/python -m pytest -q           # ~3 min, never calls a real API
.venv/bin/python -m pytest tests/test_packs.py -k AN-01    # one pack
.venv/bin/python -m casebook ci test --base origin/main     # only what the change can affect, with reruns
.venv/bin/python tools/gen_test_matrix.py                  # regenerate docs/test-matrix.md
```

Tests that need an optional dependency skip with a reason instead of failing: `claude-agent-sdk`, `openai-agents`, `mcp`, or Node with `ts/node_modules`.

The triage eval is run by hand:

```bash
ollama pull qwen2.5:1.5b
.venv/bin/python -m casebook.triage.evaluate --provider ollama --model qwen2.5:1.5b
```
