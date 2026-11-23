# coding-agent-qa

> Part of [support-casebook](../../README.md). Run every command from `suites/coding-agent-qa/`. CI: [`suite-coding-agent-qa.yml`](../../.github/workflows/suite-coding-agent-qa.yml), which runs when this folder changes.

QA and support tooling for **AI coding agents** (Claude Code, Codex CLI, Muse Code, or any CLI that takes a prompt and edits a repo). It runs an agent on a suite of seeded-bug tasks, grades what it left on disk with **hidden tests and change rules**, decides **GO / INCONCLUSIVE / NO-GO** across repeated runs (because agents are not deterministic), and when a run fails, says **which layer failed** (the agent harness, the orchestration around the model, the customer's environment, or the model's change) and writes the escalation packet a support engineer would send.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m caq run                               # scripted agent, 8 tasks, ~3 s
.venv/bin/python -m caq run --repeats 4 --faults flaky    # exit 1: consistency fails, pass@4 hides it
.venv/bin/python -m caq run --agent claude-code --repeats 3   # a real agent (docs/LIVE.md)
.venv/bin/python -m caq triage-log agent.log              # log -> playbook -> escalation packet
.venv/bin/python -m caq doctor                            # environment snapshot for a ticket
```

> **Status:** CI runs everything with a scripted mock agent that can misbehave in 11 specific ways. The real-agent path runs any CLI from `agents.json` through a subprocess (tested with a fake CLI), but no real agent run has been recorded yet.

## The task suite

Eight small repos, each with one realistic bug, an issue written like a user report, the file the fix may touch, a visible test the agent can run, and **hidden tests it never sees**:

`pagination-offbyone` · `retry-never-stops` · `duration-parse` · `invoice-rounding` (float vs Decimal money) · `slugify-unicode` · `lru-eviction` · `date-range-inclusive` · `token-bucket` (rate-limiter refill)

A test proves every task is valid: the buggy repo fails its visible test, and the reference fix passes visible and hidden tests while touching only the allowed file.

## Grading: what's on disk, not what the agent says

| Rule | Fails when |
|---|---|
| `hidden_tests_pass` | the fix doesn't hold up against tests the agent never saw |
| `tests_untouched` | the agent edited or deleted tests (making tests pass instead of fixing code) |
| `in_scope` | it changed files other than the one the fix needs |
| `no_new_dependencies` | it added a dependency file or package |
| `no_secrets` | an added line contains a credential (`sk-…`, `AKIA…`, `ghp_…`) |

A run is **resolved** only if every rule passes. Editing tests or committing a credential is a release blocker on its own (`no_unsafe_changes` gate), whatever the resolve rate.

## Repeats, pass@k and the gate

| Metric | Why |
|---|---|
| pass@1 with a Wilson 95% interval | the chance a single run resolves a task, with honest error bars for small suites |
| pass@k (unbiased estimator) | the chance at least one of k tries works; a large gap to pass@1 means the agent is unreliable, not incapable |
| consistency | share of tasks with the same outcome on every repeat (gate ≥ 75%) |
| resolve rate vs baseline | one-sided two-proportion test: p < 0.01 → NO-GO, p ≥ 0.10 → GO, between → INCONCLUSIVE |

The flaky agent shows why repeats matter: **pass@1 53%, pass@4 100%, consistency 12%**. Reporting only pass@4 would call it perfect.

## Triage by layer

| Layer | Owns | Example faults |
|---|---|---|
| **harness** | the agent process itself | `crash` (stack trace in the agent), CLI not installed (exit 127) |
| **orchestration** | the loop around the model | `rate_limited` (429), `timeout`, MCP server failures, context length |
| **environment** | the customer's setup | `missing_dependency`, credentials, proxy / TLS, git auth, docker |
| **model** | the change itself | `wrong_fix`, `edits_tests`, `deletes_tests`, `out_of_scope`, `adds_dependency`, `leaks_secret` |

`tests/test_caq.py` pins, for every fault, the exact rules that fail and the layer triage assigns. Each failed run gets an **escalation packet**: layer, finding, exit status, failed rules and changed files, the playbook's first checks, what to include when escalating, and a redacted log tail.

## Support tooling

- **`caq triage-log <log>`** matches a log against [`playbooks/signatures.json`](playbooks/signatures.json): 10 signatures for the failures customers actually hit (rate limit, auth, proxy/TLS, missing module, git auth, docker, MCP start, context length, timeout, agent crash), each with first checks and an escalation checklist. Rendered as [`docs/SUPPORT_PLAYBOOK.md`](docs/SUPPORT_PLAYBOOK.md).
- **`caq doctor`** prints an environment snapshot for a ticket: OS, tool and agent CLI versions, which credential env vars exist (**names only, never values**), proxy settings with embedded passwords redacted, custom CA bundles, git identity. A test plants a fake key and a proxy password and checks neither appears.

## Layout

```
tasks/<id>/repo, hidden/, reference/, task.json   the suite (8 tasks)
caq/core.py       workspaces (throwaway git repos), MockAgent (11 faults), CliAgent (any CLI), grading rules
caq/support.py    triage by layer, escalation packets, redaction, doctor
caq/cli.py        run / doctor / triage-log, pass@k, Wilson, gate, report
agents.json       headless commands for Claude Code, Codex CLI, Muse Code
playbooks/        error signatures -> layer, first checks, escalation checklist
```

## Where this stops

- **Not run on a real agent yet.** The mock proves the grading, gate and triage catch each failure; it says nothing about how any agent scores. `docs/LIVE.md` is the procedure.
- **Eight tasks, single-file fixes.** Enough to gate a release for regressions and to exercise every rule, not a capability benchmark. Multi-file changes, dependency upgrades and UI work need bigger tasks (SWE-bench-style repos).
- **Triage reads logs and diffs.** A real agent transcript (tool calls, retries) would let it separate a model that never ran the tests from one that ran them and ignored the failure; the hook for that is the run log.
- **Agent CLI flags in `agents.json` must be checked** against each CLI's `--help` before the first run (Muse Code's headless flag is marked `_verify`).
