# OA-01 — 429 rate limit under normal load

**Retry:** Y

Pace requests; honor Retry-After as a minimum and add jitter. Read x-ratelimit-remaining-requests vs -tokens to see which limit is binding.

Sources: https://developers.openai.com/api/docs/guides/rate-limits, https://developers.openai.com/api/docs/guides/error-codes
