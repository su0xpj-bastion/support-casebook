# Workflows

Six workflows cover everything the package does. Each is traced from the actual code. Function names link to their definitions.

| # | Workflow | Entry point | Touches the network? |
|---|---|---|---|
| 1 | [Reproduce an error pack](#1-reproduce-an-error-pack) | `python -m casebook repro <ID>` | 127.0.0.1 only |
| 2 | [Evidence-aware retry](#2-evidence-aware-retry-decision) | inside workflow 1, policy mode | 127.0.0.1 only |
| 3 | [Triage a pasted ticket](#3-triage-a-pasted-ticket) | `python -m casebook redact ticket.txt` | no |
| 4 | [Proactive exposure scan](#4-proactive-exposure-scan) | `python -m casebook exposure <dossier> --draft` | no |
| 5 | [Incident simulation and generated updates](#5-incident-simulation-and-generated-updates) | `python -m casebook repro SIM-2` / `python -m casebook.sims.artifacts` | 127.0.0.1 only (SIM-3's optional live spot check aside) |
| 6 | [Triage evaluation](#6-triage-evaluation) | `python -m casebook.triage.evaluate --model qwen2.5:1.5b` | Ollama on 127.0.0.1 |

A seventh, [live capture](#7-live-capture-opt-in), is the only path that calls a real provider API.

---

## 1. Reproduce an error pack

**Business problem.** A customer reports "429 errors". Several different failures share that status code and need different fixes (see AN-01 vs AN-03 vs OA-04). Support needs to see the exact failure, how many requests the customer's SDK really sent, and what the right verdict is.

**Trigger.** An engineer runs `python -m casebook repro AN-01` (or `all`), or pytest runs `tests/test_packs.py`.

```mermaid
sequenceDiagram
  actor Eng as Engineer
  participant CLI as cli._cmd_repro
  participant Repro as repro.run
  participant SDK as anthropic / openai SDK
  participant Mock as MockServer (Handler.do_POST)
  participant Pk as packet.from_sdk_error
  participant Rules as rules.classify

  Eng->>CLI: repro AN-01
  CLI->>Mock: start on free 127.0.0.1 port
  CLI->>Repro: run(AN-01, srv, "sdk-default")
  Repro->>Repro: packs.get, request_kwargs, make_client(max_retries=2, X-Casebook-Pack: AN-01)
  loop SDK default retries (3 attempts)
    SDK->>Mock: POST /v1/messages
    Mock->>Mock: State.record(), then expected.json mode=replay
    Mock-->>SDK: 429 + error body + headers
  end
  SDK-->>Repro: raises EnforcedSpendLimitReachedError
  Repro->>Pk: from_sdk_error(provider, exc)
  Pk-->>Repro: Support Packet (redacted)
  Repro->>Rules: classify(packet)
  Rules-->>Repro: Verdict(billing.tier_spend_cap, N, customer-admin)
  Repro-->>CLI: requests_sent=3, exception, packet, verdict
  CLI->>Repro: run(AN-01, srv, "policy")
  Note over Repro: see workflow 2: sends 1 request
  CLI-->>Eng: table row: sdk-default 3, policy 1
```

**Execution order** ([`repro.run`](../casebook/repro.py#L55)):
1. [`packs.get`](../casebook/packs.py#L46) loads `pack.yaml` and `expected.json`.
2. [`request_kwargs`](../casebook/repro.py#L41) builds the request; [`make_client`](../casebook/repro.py#L28) creates an SDK client pointed at the mock with the `X-Casebook-Pack` header and `max_retries` 2 (sdk-default) or 0 (policy).
3. `srv.state.reset()` zeroes the request counter.
4. The SDK call runs. On the mock, [`Handler.do_POST`](../casebook/mockserver.py#L166) records the request, looks up the pack, and serves the response its `mode` describes.
5. The SDK raises. `repro.run` picks the packet builder: [`from_client_error`](../casebook/packet.py#L186) for timeouts and connection errors, otherwise [`from_sdk_error`](../casebook/packet.py#L204).
6. [`rules.classify`](../casebook/rules.py#L33) returns the `Verdict`.
7. The result carries `requests_sent` from the mock's counter, all request-ids seen, elapsed time, the exception class, any partial streamed text, the packet and the verdict.

**Data transformation.** HTTP response → SDK exception → raw response body and headers → redacted packet (`http.status`, `error.type/code/details_error_code`, `request_ids`, `rate_headers`, `warnings`) → verdict.

**Success.** The verdict and request count match `pack.yaml` (`test_rules_verdict_matches_pack`, `test_request_counts_python_sdk_default_vs_policy`).

**Failure and fallback.**
- Unknown pack id: the mock replies 400 "unknown casebook pack".
- Pack provider doesn't match the route: 400.
- Stream packs in policy mode are skipped (`"stream packs: policy n/a"`), because the policy wrapper doesn't wrap streams.
- If the SDK raises before sending (AN-10), there's no packet; the scenario path in `scenarios.run` handles that pack instead.

**Outcome.** One row of the repro table: status, code, verdict, retry Y/N/C, requests sent with SDK defaults vs with the policy, and exception class.

The same packs run through the Node SDKs via [`ts_repro.run`](../casebook/ts_repro.py#L21), which shells out to `ts/repro.mjs` and reads the mock's counter the same way.

---

## 2. Evidence-aware retry decision

**Business problem.** Both providers' SDKs retry every 429 and 5xx twice by default, including billing failures the docs call non-retryable. Each retry is a wasted request and delays the real fix.

**Trigger.** `repro.run(..., "policy")`, or any caller using [`policy.call_openai`](../casebook/policy.py#L29) / [`policy.call_anthropic`](../casebook/policy.py#L54) directly.

```mermaid
sequenceDiagram
  participant Caller
  participant Pol as policy.call_anthropic
  participant SDK as anthropic SDK (max_retries=0)
  participant Dec as policy._decide
  participant Pk as packet.from_sdk_error
  participant Rules as rules.classify

  Caller->>Pol: call_anthropic(client, **kwargs)
  loop attempt in range(MAX_ATTEMPTS=3)
    Pol->>SDK: messages.create(**kwargs)
    SDK-->>Pol: raises RateLimitError / APIStatusError
    Pol->>Dec: _decide(provider, exc, attempt, trace)
    Dec->>Pk: from_sdk_error
    Dec->>Rules: classify(packet)
    alt retryable == "Y" and not last attempt
      Dec-->>Pol: delay = backoff(attempt, retry_after)
      Pol->>Pol: sleep(delay)
    else N, C, or last attempt
      Dec-->>Pol: None
      Pol-->>Caller: raise (same exception, same traceback)
    end
  end
```

**Decision logic** ([`_decide`](../casebook/policy.py#L79)):
- Retry only when the verdict's `retryable` is `"Y"` and this isn't the last of `MAX_ATTEMPTS` (3).
- Delay = [`backoff`](../casebook/policy.py#L24): `max(retry_after, 2**attempt + random())`. A server `retry-after` is a floor, never ignored and never replaced.
- Exceptions are caught in the order `RateLimitError` → `APIStatusError` → `APIError`; `APIError` without a status (connection errors) is re-raised immediately.
- Every decision is appended to `trace` (attempt, packet, verdict, sleep), which `repro.run` returns.

**Outcome.** For AN-01, OA-04, OA-05, OA-06 the policy sends 1 request where the SDK default sends 3. For retryable packs (AN-03, AN-04, OA-01…) both send 3. `test_policy_backoff_honours_retry_after` checks that every sleep is at least the server's `retry-after`.

---

## 3. Triage a pasted ticket

**Business problem.** Tickets arrive as free text, often with API keys pasted in. Support needs the secrets removed before anything is stored or sent to a model, and a first verdict that says what evidence is missing.

**Trigger.** `python -m casebook redact ticket.txt --provider anthropic`.

```mermaid
sequenceDiagram
  actor Eng as Engineer
  participant CLI as cli._cmd_redact
  participant FT as packet.from_text
  participant Red as redact.redact
  participant Rules as rules.classify

  Eng->>CLI: redact ticket.txt --provider anthropic
  CLI->>FT: from_text(text, provider)
  FT->>Red: redact(text)
  Red-->>FT: clean text, counts by kind
  FT->>FT: extract HTTP-marked statuses, known codes, req_ ids
  FT-->>CLI: packet (+ warning if no request id)
  CLI->>Rules: classify(packet)
  Rules-->>CLI: Verdict
  CLI-->>Eng: JSON {packet, verdict}
```

**Processing** ([`from_text`](../casebook/packet.py#L239)):
1. [`redact`](../casebook/redact.py#L36) runs the prefix patterns most-specific first (`sk-ant-admin01-` before `sk-ant-`), including keys wrapped across lines or JSON-escaped.
2. Statuses: an `HTTP 429`-style mention wins over stray numbers (e.g. "limit 500"). Otherwise only known API statuses are kept when several appear.
3. Known error codes are placed in the field the provider uses (`details_error_code` for Anthropic's `enforced_spend_limit_reached`, `type` for `*_error`, otherwise `code`).
4. No `req_…` id → a warning telling support to ask for the request id and a UTC timestamp.

**Decision logic.** `rules.classify` returns a status-only verdict with `retryable` `C` when the ticket has no code. A bare "429" never becomes "retry".

**Failure.** None raised; missing information becomes `None` fields and warnings.

---

## 4. Proactive exposure scan

**Business problem.** Model retirements and API changes break customer code on known dates. An account team that finds this before the customer does can send a dated, specific note instead of handling an outage.

**Trigger.** `python -m casebook exposure cases/dossiers/acme-insurer.yaml --as-of 2026-11-19 --draft`.

```mermaid
sequenceDiagram
  actor Eng as Engineer
  participant CLI as cli._cmd_exposure
  participant Scan as exposure.scan
  participant Models as models
  participant Draft as exposure.draft_note

  Eng->>CLI: exposure dossier.yaml --draft
  CLI->>Scan: scan(path, as_of)
  Scan->>Scan: _files(code_paths) → files to scan
  loop each file, each line
    Scan->>Models: retirement_for(model id), WATCH, capability sets
    Scan->>Scan: _line_rules → Exposure findings
  end
  Scan->>Scan: _file_rules (streaming without usage, SDK retries on billing 429s)
  Scan->>Scan: _dossier_rules (planned SDK upgrade, partner platforms)
  Scan->>Scan: _dedupe, sort by severity, deadline
  Scan-->>CLI: {account, as_of, files_scanned, findings}
  CLI->>Draft: draft_note(result, dossier)
  Draft-->>CLI: Markdown
  CLI-->>Eng: findings table, draft file in drafts/ (not sent)
```

**Decision logic** ([`scan`](../casebook/exposure.py#L45)):
- **Severity** from [`_sev_for_date`](../casebook/exposure.py#L40): P1 if already broken or within 14 days, P2 within 60 days, P3 otherwise.
- **Line rules** ([`_line_rules`](../casebook/exposure.py#L78)): retired or retiring model ids, forced `tool_choice` on models that reject it, `thinking.type: enabled` on 4.7+/5.x models, old computer-use tool version, sampling parameters with Claude, Assistants API calls, unguarded `chunk.choices[0]`, deprecated `connector_id`.
- **File rules** ([`_file_rules`](../casebook/exposure.py#L123)): streamed Chat Completions without `include_usage`; API calls with SDK default retries and no billing-code check.
- **Dossier rules** ([`_dossier_rules`](../casebook/exposure.py#L139)): a planned `anthropic` 1.x → 2.x upgrade; Sonnet 4.5 on Bedrock/Vertex, where retirement dates differ.

**Outcome.** A severity-sorted findings list and, with `--draft`, a Markdown note with a customer-ready paragraph for P1 items ([`draft_note`](../casebook/exposure.py#L165)). It writes to `drafts/` (gitignored) and never sends anything. `clean-co` is the control dossier and must produce zero findings (`test_exp_04_clean_integration_has_no_findings`).

---

## 5. Incident simulation and generated updates

**Business problem.** During an incident, support must decide quickly whether the provider or the customer is at fault, tell customers something accurate on a schedule, and avoid advice that makes things worse (like retrying harder).

**Trigger.** `python -m casebook repro SIM-2` (via [`scenarios.run`](../casebook/scenarios.py#L26)) or `python -m casebook.sims.artifacts` to regenerate `cases/sims/`.

SIM-2 (retry storm) as the example:

```mermaid
sequenceDiagram
  participant Art as sims.artifacts.sim2
  participant S2 as s2_retry_storm.run
  participant RS as run_strategy(strategy)
  participant W as _worker × 20 threads
  participant Mock as MockServer (mode=capacity)
  participant Gen as gen.updates

  Art->>S2: run()
  loop strategy in naive, sdk_default, budgeted
    S2->>RS: run_strategy
    RS->>Mock: start
    par 20 clients
      W->>Mock: chat.completions.create
      Mock->>Mock: _capacity_take: 10 rps for 3 s, then 60 rps
      Mock-->>W: 200 or 503 server_is_overloaded
      W->>W: retry per strategy (naive ≤10 immediate, SDK 2, budget ≤10% of calls)
    end
    RS-->>S2: amplification = requests_sent / logical_calls, success rate, recovery time
  end
  S2-->>Art: report + finding
  Art->>Gen: customer_update(P1, facts from the measured numbers)
  Art->>Gen: lint_update(text, severity="P1")
  Art-->>Art: write cases/sims/SIM-2-retry-storm/{report.json, customer-guidance.md}
```

**Other sims:**
- **SIM-1** ([`s1_outage.run`](../casebook/sims/s1_outage.py#L74)): 10 OpenAI SDK clients call two endpoints while the mock drops connections. [`s1_outage.classify`](../casebook/sims/s1_outage.py#L32) calls it provider-side when at least 60% of clients see connection errors or 5xx on at least 2 endpoints; one client with a revoked key is the control and always reads customer-side. Updates are generated and linted on a 30-minute (simulated) cadence.
- **SIM-3** ([`s3_silent_degradation.run`](../casebook/sims/s3_silent_degradation.py#L82)): a sticky slice of sessions receives corrupted answers with HTTP 200; compares hourly canaries with probes of 10% of real sessions across 20 seeds.

**Generated text.** [`customer_update`](../casebook/gen/updates.py#L25), [`escalation_brief`](../casebook/gen/updates.py#L45) and [`rca`](../casebook/gen/updates.py#L64) build Markdown from measured facts. [`lint_update`](../casebook/gen/updates.py#L76) returns a list of problems; empty means it passes.

**Failure.** Lint problems are returned, not raised, and tests assert the list is empty (`test_sim1_classifies_provider_side_and_updates_lint_clean`).

---

## 6. Triage evaluation

**Business problem.** Can an LLM help triage tickets without ever telling a customer to retry a non-retryable failure or leaking a key?

**Trigger.** `python -m casebook.triage.evaluate --provider ollama --model qwen2.5:1.5b`.

```mermaid
sequenceDiagram
  participant Ev as evaluate.run
  participant DS as dataset
  participant TR as classify.triage_rules
  participant TC as classify.triage_combined
  participant TL as classify.triage_llm
  participant LLM as Ollama (Chat Completions)
  participant M as evaluate.metrics

  Ev->>DS: synthetic() + adversarial(), split() by family
  loop each ticket, mode = rules
    Ev->>TR: triage_rules(ticket)
  end
  loop each ticket, mode = llm
    Ev->>TL: triage_llm
    TL->>LLM: chat.completions.create(json_object, temperature 0, max_tokens 200)
    LLM-->>TL: {"class", "evidence"} or invalid JSON
  end
  loop each ticket, mode = combined
    Ev->>TC: triage_combined
    TC->>TR: rules first
    alt rules decided
      TR-->>TC: result
    else needs_more_info
      TC->>TL: triage_llm
      TC->>TC: guard: retryable class without an error code, or an invented quote → needs_more_info
    end
  end
  Ev->>M: metrics per mode (all / test split / adversarial)
  Ev-->>Ev: gates, write triage/eval/results-<tag>.json and predictions-<tag>.jsonl
```

**Decision logic:**
- [`triage_rules`](../casebook/triage/classify.py#L81): four specific documented messages first, then `packet.from_text` + `rules.classify`; anything not code-confident becomes `needs_more_info` unless a documented message phrase matches.
- [`triage_llm`](../casebook/triage/classify.py#L113): redacts before building the prompt; any `APIError` or invalid JSON becomes `needs_more_info` with basis `llm error: …`, which [`metrics`](../casebook/triage/evaluate.py#L45) counts separately so a failing model never looks cautious.
- [`triage_combined`](../casebook/triage/classify.py#L132): rules first; the LLM only sees what rules couldn't decide; retryability always comes from the class table `LABELS`, never from model text.

**Gates.** `false_retryable == 0` and `redaction_leaks == 0` per mode. Rules and combined pass for both models; LLM alone fails the first gate. Committed results are re-checked by `test_committed_report_gates`.

---

## 7. Live capture (opt-in)

**Trigger.** `python -m casebook live OA-07 AN-13 AN-16 --yes`. Packs outside `live.FREE` need `--keyed` and the user's own key.

[`live.capture`](../casebook/live.py#L54) sends one real request with retries off (free packs use a deliberately fake key), redacts headers and body, builds a packet and verdict, diffs the response field by field against `expected.json` ([`live.diff`](../casebook/live.py#L91)), and writes `faults/<provider>/<pack>/live/<date>-python.json`. Without `--yes` the CLI refuses to send. `tests/test_live_captures.py` checks committed captures offline: well-formed, and no secret survives redaction.
