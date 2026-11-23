"""stream_latency.py — measure how fast an OpenAI chat completion streams.

Given one prompt on the command line, this script:
  1. opens a streaming chat completion against the OpenAI API,
  2. records a timestamp every time a new token arrives,
  3. computes four latency metrics, and
  4. prints the result as JSON.

Metrics it reports
------------------
  ttft           : Time-To-First-Token. How long the user waits before
                   they see *anything* on screen. Seconds.
  tbt            : Time Between Tokens. Average gap between consecutive
                   tokens after the first one. Seconds. Lower means a
                   smoother streaming experience.
  total_latency  : Wall-clock time from request start to last token.
                   Seconds.
  token_count    : Number of streamed chunks. The exercise tells us to
                   treat one chunk as one token.

Usage
-----
  $ python stream_latency.py "Summarize the benefits of solar power."
  {
    "ttft": 0.18,
    "tbt": 0.04,
    "total_latency": 0.92,
    "token_count": 19
  }
"""
from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import asdict, dataclass

import openai
from dotenv import load_dotenv

# Which OpenAI model to call. Module-level constant so the value is
# set once and reused — easy to swap by editing one line.
MODEL = "gpt-6-luna"

# How many times we'll attempt the API call before giving up. Includes
# the first try (so 3 = first attempt + 2 retries).
MAX_ATTEMPTS = 3


@dataclass
class LatencyMetrics:
    """The four numbers we report back to the caller.

    Using @dataclass gives us a clean constructor and a free dict
    conversion (via dataclasses.asdict) for JSON serialization, without
    writing the boilerplate by hand.
    """

    ttft: float            # seconds — request start → first token
    tbt: float             # seconds — average gap between tokens
    total_latency: float   # seconds — request start → last token
    token_count: int       # number of streamed chunks (chunk == token)


def compute_metrics(
    start: float,
    timestamps: list[float],
    last: float,
) -> LatencyMetrics:
    """Pure math: turn raw timestamps into the four reported metrics.

    "Pure" means this function does no I/O, makes no API calls, and has
    no side effects. The same inputs always produce the same outputs,
    which makes it trivial to unit-test without hitting the network.

    Args:
        start:      Wall-clock moment we sent the request (seconds).
        timestamps: One entry per content chunk received (seconds each).
        last:       Wall-clock moment the stream ended (seconds).

    Returns:
        A LatencyMetrics with ttft, tbt, total_latency, and token_count.
    """
    # token_count comes from how many chunks we observed. The exercise
    # tells us to treat one chunk as one token.
    token_count = len(timestamps)

    # Total time the user waited, request to final token. The max(0, ...)
    # is defensive — clocks should always move forward, but we don't
    # want a freak negative value to leak out.
    total_latency = max(0.0, last - start)

    # Edge case: the model returned no content at all (immediate filter
    # or empty completion). Report zeros for the per-token numbers so
    # the JSON output is still well-formed.
    if not timestamps:
        return LatencyMetrics(
            ttft=0.0,
            tbt=0.0,
            total_latency=total_latency,
            token_count=token_count,
        )

    # Time-To-First-Token: gap between request start and the first chunk.
    ttft = max(0.0, timestamps[0] - start)

    # Time-Between-Tokens: only meaningful with two or more tokens, since
    # we need at least one gap to measure. With one token, report 0.0.
    if len(timestamps) >= 2:
        gaps = [
            timestamps[i] - timestamps[i - 1]
            for i in range(1, len(timestamps))
        ]
        tbt = sum(gaps) / len(gaps)
    else:
        tbt = 0.0

    return LatencyMetrics(
        ttft=ttft,
        tbt=tbt,
        total_latency=total_latency,
        token_count=token_count,
    )


def measure_latency(prompt: str) -> LatencyMetrics:
    """Stream a chat completion and time every chunk that arrives.

    This function does the real work: it opens the network stream,
    records timestamps as chunks arrive, then hands the raw numbers off
    to compute_metrics() for the actual arithmetic.

    Args:
        prompt: The user's question or instruction to send to the model.

    Returns:
        LatencyMetrics describing timing for that one call.
    """
    # Build a fresh OpenAI client. The API key is read from the
    # OPENAI_API_KEY environment variable — main() runs load_dotenv()
    # first so that variable is set before we get here.
    client = openai.OpenAI()

    # The conversation we send. "system" frames the assistant's role,
    # "user" carries the caller's prompt text.
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant.",
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    # Declared *outside* the retry loop so they survive across attempts.
    # Per the seed: usage and finish_reason live at function-body scope,
    # never inside the for-attempt loop, so the cost / warn blocks below
    # always have a defined value to read.
    usage = None                             # token totals (final chunk)
    finish_reason: str | None = None         # why the stream ended

    # Retry loop: tries the API up to MAX_ATTEMPTS times. We only retry
    # on RateLimitError (transient 429). Any other API error bubbles up
    # immediately so the caller sees the real problem.
    for attempt in range(MAX_ATTEMPTS):
        # Reset per-attempt state. timestamps and t_start must reset so
        # a delayed retry doesn't pollute the timing measurement with
        # the previous attempt's clock or chunks.
        finish_reason = None
        timestamps: list[float] = []
        t_start = time.perf_counter()

        try:
            # The `with` block (a context manager) guarantees the
            # network stream is closed even if something raises mid-loop.
            with client.chat.completions.create(
                model=MODEL,
                messages=messages,
                stream=True,                              # streamed reply
                stream_options={"include_usage": True},   # token totals at end
            ) as stream:
                # Three distinct things can arrive on each chunk: a
                # piece of text content, a finish_reason (near the end),
                # and a usage object (in the very last chunk, where
                # choices is empty). Three separate if-blocks let each
                # one land in the right bucket.
                for chunk in stream:
                    # 1. New token of text → record the moment it arrived.
                    if chunk.choices and chunk.choices[0].delta.content:
                        timestamps.append(time.perf_counter())
                    # 2. Stream signals it's done → capture *why* it stopped.
                    if chunk.choices and chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    # 3. Final chunk carries the API's own token totals.
                    if chunk.usage:
                        usage = chunk.usage

            # Stamp the moment the stream finished.
            t_last = time.perf_counter()

            # If the stream ended for any reason other than "stop" (e.g.
            # "length" = truncated, "content_filter" = blocked), surface
            # it on stderr so the JSON on stdout stays clean.
            if finish_reason and finish_reason != "stop":
                print(
                    f"[WARN] stream ended with "
                    f"finish_reason={finish_reason!r}",
                    file=sys.stderr,
                )

            # Sanity check: API-reported completion tokens vs our chunk
            # count. Useful when the chunk-vs-token assumption stretches.
            if usage:
                print(
                    f"[INFO] api completion_tokens={usage.completion_tokens}, "
                    f"chunk count={len(timestamps)}",
                    file=sys.stderr,
                )

            # Success: hand raw numbers to the pure helper for the math.
            return compute_metrics(t_start, timestamps, t_last)

        except openai.RateLimitError:
            # Transient 429 — most specific exception goes first because
            # RateLimitError is a subclass of APIStatusError. Catching
            # APIStatusError first would swallow rate limits silently.
            # On the final attempt give up and let main() format the
            # error. Bare `raise` preserves the original traceback.
            if attempt == MAX_ATTEMPTS - 1:
                raise
            # Exponential backoff with jitter: 1s±, 2s±, 4s±... The
            # jitter spreads simultaneous retries so we don't all hit
            # the API in lockstep after a thundering-herd outage.
            time.sleep(2 ** attempt + random.random())
        except openai.APIStatusError:
            # 4xx / 5xx other than 429 — don't retry, surface up.
            raise
        except openai.APIError:
            # Network or transport errors — don't retry, surface up.
            raise

    # Defensive: every path above either returns or raises, so this is
    # unreachable. Kept as a safety net for future refactors.
    raise RuntimeError("retry loop exhausted without return or raise")


def main() -> None:
    """CLI entry point: read the prompt from argv, measure, print JSON.

    load_dotenv() and the OpenAI client live inside main() (not at
    module level) because side effects belong in the entry point.
    Importing this file from another script should not silently mutate
    the environment or open a network client.
    """
    # Load OPENAI_API_KEY (and anything else) from a local .env file.
    load_dotenv()

    # We expect exactly one argument: the prompt string.
    if len(sys.argv) != 2:
        raise SystemExit('usage: python stream_latency.py "prompt"')

    prompt = sys.argv[1]

    # Wrap the API call so a clean [ERROR] line lands on stderr instead
    # of a Python traceback. Same exception order as measure_latency:
    # RateLimitError first (most specific), APIStatusError, APIError.
    try:
        metrics = measure_latency(prompt)
    except openai.RateLimitError:
        print(
            f"[ERROR] rate limit exceeded after {MAX_ATTEMPTS} attempts",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except openai.APIStatusError as exc:
        print(
            f"[ERROR] status {exc.status_code}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except openai.APIError as exc:
        print(f"[ERROR] API error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    # Pretty-print the dataclass as JSON for the user or downstream tools.
    print(json.dumps(asdict(metrics), indent=2))


if __name__ == "__main__":
    main()
