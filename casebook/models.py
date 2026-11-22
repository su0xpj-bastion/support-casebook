"""Model lifecycle and capability facts, as published on 2026-11-19.

One table used twice: the mock enforces it (so migrations fail the way the docs say), and the
exposure scanner reads it (so customers hear about a break before it happens).

Sources:
  Anthropic  https://platform.claude.com/docs/en/about-claude/model-deprecations
             https://platform.claude.com/docs/en/api/errors
  OpenAI     https://developers.openai.com/api/docs/deprecations
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

AS_OF = date(2026, 11, 19)


@dataclass(frozen=True)
class Retirement:
    model: str            # exact id or prefix (prefix=True)
    provider: str
    retires: date         # Anthropic "retired" / OpenAI "shutdown"
    replacement: str | None
    prefix: bool = False
    note: str = ""

    def matches(self, model_id: str) -> bool:
        return model_id.startswith(self.model) if self.prefix else model_id == self.model


RETIREMENTS: list[Retirement] = [
    # Anthropic — deprecated, retiring soon
    Retirement("claude-sonnet-4-5", "anthropic", date(2026, 11, 30), "claude-sonnet-5-5", prefix=True,
               note="covers claude-sonnet-4-5-20250929 and the undated alias; partner platforms have their own dates"),
    # Anthropic — already retired
    Retirement("claude-opus-4-1-20250805", "anthropic", date(2026, 8, 5), "claude-opus-5-5"),
    Retirement("claude-opus-4-20250514", "anthropic", date(2026, 6, 15), "claude-opus-5-5"),
    Retirement("claude-sonnet-4-20250514", "anthropic", date(2026, 6, 15), "claude-sonnet-5-5"),
    Retirement("claude-3-7-sonnet-20250219", "anthropic", date(2026, 2, 19), "claude-sonnet-5-5"),
    Retirement("claude-3-5-haiku-20241022", "anthropic", date(2026, 2, 19), "claude-haiku-5-5"),
    Retirement("claude-3-haiku-20240307", "anthropic", date(2026, 4, 20), "claude-haiku-5-5"),
    # OpenAI
    Retirement("gpt-4o-mini", "openai", date(2027, 1, 14), None, prefix=True),
    Retirement("gpt-4o", "openai", date(2027, 1, 14), None, prefix=True),
    Retirement("gpt-4.1-mini", "openai", date(2027, 1, 14), None, prefix=True),
    Retirement("gpt-4.1", "openai", date(2027, 1, 14), None, prefix=True),
    Retirement("gpt-5.1", "openai", date(2027, 4, 1), None),
]

# "Not sooner than" dates are notices, not retirements — surfaced as watch items.
WATCH: dict[str, str] = {
    "claude-opus-4-5-20251101": "retirement not sooner than 2026-11-24",
    "claude-haiku-4-5-20251001": "retirement not sooner than 2026-10-15 (still Active on 2026-11-19)",
}

# Newest models: forced tool_choice ("any"/"tool") returns 400.
NO_FORCED_TOOL_CHOICE = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2",
                         "claude-mythos-5-1", "claude-mythos-5-2"}
# Claude 4.6+ (and all 5.x): assistant prefill returns 400.
NO_PREFILL_PREFIXES = ("claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-7", "claude-opus-4-8",
                       "claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5", "claude-mythos")
# Claude 4.7+: thinking.type "enabled" returns 400 (use adaptive + output_config.effort).
NO_THINKING_ENABLED_PREFIXES = ("claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
                                "claude-haiku-5", "claude-fable-5", "claude-mythos")
COMPUTER_TOOL_CURRENT = "computer_toolset_20260801"
SAMPLING_PARAMS = ("temperature", "top_p", "top_k")
API_VERSION_NO_SAMPLING = "2026-10-21"


def retirement_for(model_id: str) -> Retirement | None:
    for r in RETIREMENTS:
        if r.matches(model_id):
            return r
    return None


def is_retired(model_id: str, on: date) -> bool:
    r = retirement_for(model_id)
    return r is not None and on >= r.retires


def no_prefill(model_id: str) -> bool:
    return model_id.startswith(NO_PREFILL_PREFIXES)


def no_thinking_enabled(model_id: str) -> bool:
    return model_id.startswith(NO_THINKING_ENABLED_PREFIXES)
