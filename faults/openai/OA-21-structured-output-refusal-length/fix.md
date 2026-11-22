# OA-21 — Structured output: refusal parses to None; length raises before parsing

**Retry:** N

Check message.refusal and finish_reason before using parsed output; raise max_completion_tokens if length.

Sources: https://developers.openai.com/api/docs/guides/structured-outputs
