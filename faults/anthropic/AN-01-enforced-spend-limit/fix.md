# AN-01 — Monthly tier spend cap — 429 with no retry-after; retries are useless

**Retry:** N

Request a usage-tier increase (account team / Console) or wait until 00:00 UTC on the 1st. Disable SDK auto-retry for this error_code: the SDK retries every 429 twice.

Sources: https://platform.claude.com/docs/en/api/rate-limits, https://platform.claude.com/docs/en/api/errors
