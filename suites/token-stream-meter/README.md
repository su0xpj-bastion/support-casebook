# token-stream-meter

> Part of [support-casebook](../../README.md). Run every command from `suites/token-stream-meter/`. CI: [`suite-token-stream-meter.yml`](../../.github/workflows/suite-token-stream-meter.yml), which runs when this folder changes.

A small CLI that measures how fast an OpenAI Chat Completions response streams. It timestamps every content chunk with a monotonic clock and prints four numbers as JSON:

| Metric | Meaning |
|---|---|
| `ttft` | Time to first token: request start → first content chunk (s) |
| `tbt` | Time between tokens: mean gap between consecutive content chunks (s) |
| `total_latency` | Request start → end of stream (s) |
| `token_count` | Content chunks received (one chunk is treated as one token) |

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set OPENAI_API_KEY
.venv/bin/python stream_latency.py "Summarize the benefits of solar power."
```

It prints `{"ttft": …, "tbt": …, "total_latency": …, "token_count": …}` on stdout; warnings and the usage cross-check go to stderr.

## What it gets right

- **Only content chunks are timed.** The final `finish_reason` chunk and the `include_usage` chunk (which has `choices: []`) are not counted as tokens, and the usage chunk never crashes the loop.
- **Retries don't distort the measurement.** Only 429 is retried (3 attempts, `2**attempt + U(0,1)` s backoff). The clock and the chunk list reset on every attempt, so backoff time never ends up inside TTFT.
- **Cross-check.** With `stream_options={"include_usage": true}`, the API's `completion_tokens` is printed next to the chunk count on stderr, so you can see when "one chunk = one token" stops holding.
- **Truncation is visible.** A `finish_reason` other than `stop` (`length`, `content_filter`) prints a `[WARN]` on stderr; stdout stays clean JSON.
- **Errors exit cleanly.** `RateLimitError` → `APIStatusError` → `APIError`, most specific first, each with a one-line message and exit code 1.

## Tests

```bash
.venv/bin/python -m pytest      # 18 tests, no network, no API key
```

The OpenAI client and `time.perf_counter` are both faked, so every timing assertion is exact: TTFT/TBT math and edge cases (0 and 1 token, a clock that goes backwards), which chunks count, `stream_options` on the request, warnings, retry count and backoff values, TTFT unaffected by a retried attempt, no retry on 5xx or connection errors, and CLI exit codes.

## Where this stops

- One request, one prompt. For percentiles, concurrency and SLO gates see [inference-platform-qa](https://github.com/su0xpj-bastion/inference-qa-lab/tree/main/inference-platform-qa) and [llm-serving-validation](https://github.com/su0xpj-bastion/inference-qa-lab/tree/main/llm-serving-validation).
- Client-side wall clock: network time is inside TTFT. Server-side timing needs a provider that returns it (for example Cerebras `time_info`, see [inference-endpoint-scenarios](https://github.com/su0xpj-bastion/inference-qa-lab/tree/main/inference-endpoint-scenarios)).
- Not yet run against the live API; all results so far come from the test suite.
