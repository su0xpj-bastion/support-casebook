"""Evidence-aware retry wrappers.

Both official SDKs retry every 429 and every >=500 twice by default (unless the server sends
`x-should-retry`). That includes failures the docs call non-retryable: OpenAI
`credit_balance_exhausted` / `*_spend_limit_exceeded` / `organization_usage_limit_exceeded`
and Anthropic `enforced_spend_limit_reached`. These wrappers turn SDK retries off and decide
per attempt from the Support Packet, so a non-retryable failure costs exactly one request.
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable

import anthropic
import openai

from casebook import packet as pk
from casebook import rules

MAX_ATTEMPTS = 3


def backoff(attempt: int, retry_after: float | None) -> float:
    """Jittered exponential backoff; a server retry-after is a floor, not a replacement."""
    return max(retry_after or 0.0, 2 ** attempt + random.random())


def call_openai(
    client: openai.OpenAI,
    *,
    trace: list[dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    **create_kwargs: Any,
) -> Any:
    trace = trace if trace is not None else []
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.chat.completions.create(**create_kwargs)
        except openai.RateLimitError as exc:
            delay = _decide("openai", exc, attempt, trace)
            if delay is None:
                raise
        except openai.APIStatusError as exc:
            delay = _decide("openai", exc, attempt, trace)
            if delay is None:
                raise
        except openai.APIError:
            raise
        sleep(delay)
    raise AssertionError("unreachable")


def call_anthropic(
    client: anthropic.Anthropic,
    *,
    trace: list[dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    **create_kwargs: Any,
) -> Any:
    trace = trace if trace is not None else []
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.messages.create(**create_kwargs)
        except anthropic.RateLimitError as exc:
            delay = _decide("anthropic", exc, attempt, trace)
            if delay is None:
                raise
        except anthropic.APIStatusError as exc:
            delay = _decide("anthropic", exc, attempt, trace)
            if delay is None:
                raise
        except anthropic.APIError:
            raise
        sleep(delay)
    raise AssertionError("unreachable")


def _decide(provider: str, exc: Any, attempt: int, trace: list[dict[str, Any]]) -> float | None:
    """Return the sleep before the next attempt, or None to stop and re-raise."""
    p = pk.from_sdk_error(provider, exc)
    verdict = rules.classify(p)
    last = attempt == MAX_ATTEMPTS - 1
    delay = None if (verdict.retryable != "Y" or last) else backoff(attempt, p["http"]["retry_after"])
    trace.append({"attempt": attempt + 1, "packet": p, "verdict": verdict.as_dict(), "sleep": delay})
    return delay
