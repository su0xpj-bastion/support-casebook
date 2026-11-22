# OA-10 — 503 server_is_overloaded surfaces as InternalServerError, not RateLimitError

**Retry:** Y

Retry with backoff honoring Retry-After; check status.openai.com. Code that only catches RateLimitError will crash on this — catch APIStatusError and branch on error.code.

Sources: https://developers.openai.com/api/docs/guides/error-codes
