# deep-research-eval

> Part of [support-casebook](../../README.md). Run every command from `suites/deep-research-eval/`. CI: [`suite-deep-research-eval.yml`](../../.github/workflows/suite-deep-research-eval.yml), which runs when this folder changes.

A release gate for **deep-research agents** (systems that search the web over many steps and return a sourced answer) on a fixed 60-question slice of Google DeepMind's **[DeepSearchQA](https://huggingface.co/datasets/google/deepsearchqa)**. It runs Perplexity Sonar, OpenAI (Responses + `web_search`) or Claude (Messages + `web_search`) on the same questions, grades every answer, and decides **GO / INCONCLUSIVE / NO-GO** against the system's own baseline with a statistical test, not a single number compared by eye.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m dre run                                  # simulated agent vs its baseline
.venv/bin/python -m dre run --faults drops_items             # exit 1: NO-GO, recall gate fails
.venv/bin/python -m dre run --repeats 3                      # adds a consistency check
.venv/bin/python -m dre run --system perplexity --limit 20   # live, needs PERPLEXITY_API_KEY
.venv/bin/python -m dre.judge reports/latest/results.json --judge anthropic   # grader vs LLM judge
```

> **Status:** CI runs the full pipeline against a seeded simulated agent. The live systems are implemented with the official SDKs but no live run has been recorded yet, so every number on this page is from the simulation.

## Why DeepSearchQA

900 hand-written tasks across 17 fields, each a chain of dependent lookups, and 65% of them ask for a **complete list** ("which five trusts…", "all the years between…"). A list answer can be right, incomplete, padded with wrong items, or wrong, and those are different failures with different fixes. Perplexity's own `search_evals` uses it as one of its four benchmarks.

The slice (`data/dsqa_slice.jsonl`) is 60 items, seeded and stratified to keep the 35 / 65 single / list split and cover 17 categories, vendored at a pinned dataset commit with its Apache-2.0 attribution (`data/SOURCE.md`, `scripts/vendor_dsqa.py`). As the dataset card requires, the answer type is used for grading only and never sent to the model.

## What a run reports

| Metric | Why it's there |
|---|---|
| **Fully correct**, with a Wilson 95% interval | the headline; 60 questions give an interval about ±12 points wide, so the interval is shown, not just the rate |
| Single-answer accuracy | the 35% that have one answer |
| List F1 / precision / recall | separates *incomplete* lists (low recall) from *padded* lists (low precision) |
| Failure kinds | correct · incomplete · extra_items · wrong · no_answer, per question |
| FINAL ANSWER format rate, citation rate | an answer the pipeline can't parse, or one with no sources, is a product failure even if the text is right |
| Latency p50, searches per question | what the answer cost in time and tool calls |
| Cost and **cost per correct answer** | from token usage and the provider's price; prices not recorded show as n/a |
| Consistency across repeats | share of questions with the same verdict on every repeat (`--repeats K`) |

## Gates and the faults that prove them

| Check | Rule | Fault it catches |
|---|---|---|
| `accuracy_vs_baseline` | one-sided two-proportion z-test vs `baselines/<system>.json`: p < 0.01 → **NO-GO**, p ≥ 0.10 → **GO**, between → **INCONCLUSIVE** | `drops_items`, `hallucinated_extras`, `wrong_singles` |
| `list_recall_vs_baseline` | list recall drops ≤ 0.10 | `drops_items` |
| `list_precision` | mean list precision ≥ 0.80 (wrong items mixed in) | `hallucinated_extras` |
| `format_compliance` | ≥ 95% end with `FINAL ANSWER:` | `format_drift` |
| `citations_present` | ≥ 90% cite a source | `no_citations` |
| `consistency` | ≥ 80% stable across repeats | `flaky` |

Simulated results (healthy agent: 40/60 fully correct, list precision 0.95, recall 0.88):

```
drops_items          23/60  ❌ accuracy NO-GO (p=0.001)  ❌ list recall 0.55 vs 0.88
hallucinated_extras  17/60  ❌ list precision 0.62       ❌ accuracy NO-GO (p<0.001)
wrong_singles        25/60  ❌ accuracy NO-GO (p=0.003)
format_drift         40/60  ❌ format 0%   (accuracy unchanged: only the format gate can see it)
no_citations         40/60  ❌ citations 0%
flaky, 3 repeats            ❌ consistency 47%
```

`tests/test_eval.py` pins the exact set of failing checks for every fault, and that a healthy run passes everything.

## The grader, and how its error is measured

The official evaluation uses an LLM judge with the gold answer. This repo grades deterministically by default (free, reproducible, runs in CI): parse the `FINAL ANSWER:` line, split lists on `;`, `,`, newlines and "and"/"or" but never inside parentheses (gold items like *Rebecca West (1969, 1970)*), drop headings, then match items on normalized text, where numbers must agree exactly. Every gold answer in the slice grades correct against itself (a test).

A deterministic grader can mark a correct paraphrase wrong, so its error rate is **measured, not assumed**: `python -m dre.judge` re-grades a finished run with an LLM judge (Claude or OpenAI, strict JSON verdict) and reports raw agreement, Cohen's kappa, and every disagreement. On the simulated run against the lenient offline judge: 97% agreement, kappa 0.92, with two list answers the strict grader marked wrong that the judge accepted.

## Where this stops

- **Not run live yet.** The simulated agent proves the gates fire on the right failures; it says nothing about how Perplexity, OpenAI or Claude score. A live run of 20 questions costs a few dollars on most systems; deep-research tiers cost more.
- **60 questions is a release gate, not a leaderboard.** It detects large regressions (the faults move accuracy 15–23 points); a 3-point difference needs the full 900.
- **The web changes.** DeepSearchQA assumes a static web; answers can drift as pages change, which is one reason the gate compares a system with its *own* recent baseline.
- Outcome-only grading: like the benchmark, this treats the agent as a black box. Search-trajectory quality (which pages, how many dead ends) is reported only as a search count.
