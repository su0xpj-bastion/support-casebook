# SIM-2 — Retry storm — the same demand, three client retry strategies, one capacity incident

*Provider:* openai · *surface:* api · *kind:* sim · *retry:* - · *weakest provenance:* `synthesized`

## Confirm it
- requests per logical call
- client retry settings
- recovery time after capacity returned

Easily confused with: OA-01, OA-03, OA-10

## Fix and customer guidance
Customer guidance, backed by the measured amplification: turn off unbounded app-level retries, keep SDK retries or a jittered backoff, and cap retries with a budget (for example, retries at most 10% of calls). Failed requests count toward limits, so retries during an incident lengthen it.

Sources: https://developers.openai.com/api/docs/guides/rate-limits

## Escalate when
Escalate to the provider/platform incident channel with request IDs, UTC window and blast radius once two or more customers or keys show it.

## Provenance
- failed_requests_count_toward_limits: doc-verbatim
- capacity_numbers: synthesized
- incident_shape: doc-partial

Reproduce: `python -m casebook repro SIM-2`
