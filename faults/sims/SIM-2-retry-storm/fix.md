# SIM-2 — Retry storm

Customer guidance, backed by the measured amplification: turn off unbounded app-level retries, keep SDK retries or a jittered backoff, and cap retries with a budget (for example, retries at most 10% of calls). Failed requests count toward limits, so retries during an incident lengthen it.

Sources: https://developers.openai.com/api/docs/guides/rate-limits
