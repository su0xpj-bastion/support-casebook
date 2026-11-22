# OA-02 — 429 slow_down: ramped too fast, dashboard shows headroom

**Retry:** Y

Honor Retry-After, reduce, then ramp gradually: past 1M input TPM increase by no more than 50% every 15 minutes. Not a tier problem.

Sources: https://developers.openai.com/api/docs/guides/rate-limits, https://developers.openai.com/api/docs/guides/error-codes
