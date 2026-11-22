# AN-04 — 429 on a sub-minute burst under the per-minute limit

**Retry:** Y

Spread requests evenly (jittered backoff, client-side pacing). The per-minute number is not a per-minute budget you can burst.

Sources: https://platform.claude.com/docs/en/api/rate-limits
