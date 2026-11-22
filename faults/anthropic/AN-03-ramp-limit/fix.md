# AN-03 — 429 ramp_limit_exceeded while under ITPM/OTPM limits

**Retry:** Y

Wait retry-after, hold traffic steady, grow at most one step per minute; start bulk jobs slowly or move them to the Batches API (exempt from ramp limits).

Sources: https://platform.claude.com/docs/en/api/rate-limits
