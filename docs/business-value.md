# Business value and use cases

## The problem, in plain terms

Companies that build on the OpenAI and Anthropic APIs hit failures that look the same but need different fixes. A "429 error" can mean "slow down and retry", "your monthly spend cap is reached, retrying will never work", or "your prepaid credits ran out". Some failures return no error at all: the request succeeds but is billed to the wrong account, served at a cheaper tier, or quietly degraded.

When support guesses wrong, the customer retries something that can't succeed, the outage lasts longer, and the ticket bounces between teams. This repo shows how to tell these failures apart from evidence and turn that evidence into the right action and the right message.

## What this repository measured

These are measurements taken by running the code in this repo against a local mock that follows the providers' documented behaviour. They aren't measurements of a real support team or real customers. There were none.

| Metric | Baseline | With this approach | Source |
|---|---|---|---|
| Requests sent per user action on a non-retryable billing failure (AN-01, OA-04, OA-05, OA-06) | 3 (SDK default) | 1 (evidence-aware policy) | `test_request_counts_python_sdk_default_vs_policy` |
| Server-side copies of work after a timeout (AN-11, OA-14) | 3 | 1 | same |
| Request amplification during a capacity incident (SIM-2) | naive retries ~9.95×, SDK default ~1.75× | retry budget ~1.04× | [`cases/sims/SIM-2-retry-storm/report.json`](../cases/sims/SIM-2-retry-storm/report.json) |
| Recovery after capacity returned (SIM-2) | naive: not within the window; SDK default: 2–5 s | retry budget: 1 s | same |
| Detection of a 0.18% silent-degradation slice (SIM-3) | hourly canaries: 0% of runs | 10% per-session probes: 90% of runs | [`cases/sims/SIM-3-silent-degradation/report.json`](../cases/sims/SIM-3-silent-degradation/report.json) |
| Time to classify an outage as provider-side from client signals (SIM-1) | — | 5 simulated minutes after impact | [`cases/sims/SIM-1-provider-outage/report.json`](../cases/sims/SIM-1-provider-outage/report.json) |
| Tickets wrongly marked "retry" on non-retryable failures | small LLM alone: 25 (qwen2.5:1.5b), 49 (gemma3:1b) of 260 | rules or rules + guarded LLM: 0 | [`triage/eval/REPORT.md`](../triage/eval/REPORT.md) |
| Secrets reaching a model or output in the triage eval | — | 0 of 55 planted keys | same |

**Not measured, and not claimed:** time saved per ticket, reduction in repeat contacts, cost saved, customer satisfaction, adoption.

## What is automated

| Manual step today | What this repo automates | Still manual |
|---|---|---|
| Reading an error and guessing whether to retry | `rules.classify` gives the class, retry Y/N/C, owner and the doc link from the packet | Confirming with the customer; anything `C` or `needs_more_info` |
| Scrubbing keys out of pasted tickets and logs | `redact` (including keys wrapped across lines and inside base64 headers) | Unknown secret formats |
| Asking the customer for the right evidence | Packet warnings name what's missing (request id, UTC timestamp, `X-Client-Request-Id`) | Sending the request |
| Reproducing a reported failure | `python -m casebook repro <ID>` through the real SDKs | Failures without a pack |
| Writing incident updates and RCAs | `gen.updates` builds them from measured facts; `lint_update` blocks unsafe ones | Review and sending |
| Checking a customer's code for upcoming breaking changes | `exposure.scan` + `draft_note` | Sending the note; dossier upkeep |
| Writing runbooks | `gen.runbooks` writes one per pack | Review |
| Noticing docs moved | Weekly `doc-drift` workflow | Fixing the link |

## Operating cost

| Item | Cost |
|---|---|
| Running the code and tests | A laptop; no services |
| CI | GitHub Actions on a macOS runner, ~4 minutes per push. Free for public repositories; billed by the minute on private ones |
| Triage eval | Free local models through Ollama |
| Live captures | Free packs use a fake key and cost nothing. Keyed packs cost one small API request each (not run) |
| Maintenance | Pack facts go stale when providers change behaviour or retire models. Weekly link checks catch moved docs, not changed behaviour |

## ROI model

There is no real team data, so this is a model to fill in, not a result. Every input below is an **assumption** to be replaced with a real support team's numbers.

```
Hours saved per week  = tickets per week × share that match a pack × minutes saved per matched ticket ÷ 60
Annual labor savings  = hours saved per week × 52 × loaded hourly cost
Annual net benefit    = annual labor savings + avoided customer spend − annual operating cost
ROI (%)               = (annual net benefit − initial investment) ÷ initial investment × 100
```

In plain English: count how many tickets look like one of these failures, estimate how much faster each one gets answered when the evidence and verdict are already there, and turn the hours into money. Then subtract what it costs to keep the packs current.

Worked example with **assumed** inputs (not measurements):

| Input | Assumed value |
|---|---|
| Tickets per week | 200 |
| Share matching a pack | 20% → 40 tickets |
| Minutes saved per matched ticket | 15 |
| Loaded hourly cost | $100 |
| Upkeep | 4 hours per week |
| Initial investment | 80 hours |

- Hours saved per week = 40 × 15 ÷ 60 = 10
- Annual labor savings = 10 × 52 × $100 = $52,000
- Annual operating cost = 4 × 52 × $100 = $20,800
- Annual net benefit = $52,000 − $20,800 = $31,200
- Initial investment = 80 × $100 = $8,000
- ROI = ($31,200 − $8,000) ÷ $8,000 × 100 = 290%

Change any assumption and the result changes with it: at 5% of tickets matching, the same model gives a net benefit of −$7,800 (a loss). The model leaves out avoided customer spend (for example, retries that would have failed anyway), which the repro counts show exists but can't be priced without real traffic.

## Use cases

### Verified: each one runs in this repo

| Persona | Problem | Before | With this repo | Components | Packs |
|---|---|---|---|---|---|
| Support engineer | "We get 429s" | Ask for logs, guess, suggest backoff | Paste the ticket: secrets removed, verdict says spend cap vs rate limit, and what evidence is missing | `packet.from_text`, `rules.classify` | AN-01, AN-03, OA-01, OA-04 |
| Support engineer | Customer's retries make an incident worse | General advice | Measured amplification per retry strategy and a linted P1 update with the numbers | `sims.s2_retry_storm`, `gen.updates` | SIM-2 |
| Support engineer | "Answers got worse, no errors" | Ask for examples | Detection method that finds a 0.18% slice, plus the update asking for request ids by platform | `sims.s3_silent_degradation` | SIM-3 |
| Account team (CSM / AE) | A model the customer uses retires soon | Customer finds out when calls fail | Dated findings with file and line, and a draft note | `exposure.scan`, `exposure.draft_note` | AN-26, AN-18–21 |
| Customer's platform engineer | Headless agent job reports success but did nothing | Logs look green | Shows the denied-tool result inside a `success` result; what to monitor instead | `agent_sdk_scenarios.ag22` | AG-22 |
| Customer's identity admin | SSO broke after an IdP certificate rotation | Trial and error in the IdP console | Exact failure class (`saml.signature_invalid`, `saml.idp_cert_expired`) and the fix | `identity` | ID-04, AN-33 |
| Enterprise IT | CLI hangs behind a corporate proxy | No error to search for | The hang reproduced, the variable that fixes it | `client_lab` | AN-50, AN-51, OA-48 |

Failure and fallback for all of these: when the evidence doesn't decide, the verdict is `C` or `needs_more_info` with a warning naming what to ask for. Nothing is sent to a customer automatically.

### Proposed: not built

- Ingest tickets from a real helpdesk and attach the packet automatically.
- Run the exposure scan on every dossier on a schedule and open a task when a P1 appears.
- Group incoming tickets by verdict class to spot a provider incident early (SIM-1's classifier applied to real tickets).
