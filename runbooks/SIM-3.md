# SIM-3 — Silent quality degradation on a sticky slice — canary evals vs per-session probes

*Provider:* anthropic · *surface:* api · *kind:* sim · *retry:* - · *weakest provenance:* `synthesized`

## Confirm it
- request-ids by platform and date
- per-session quality signal
- platform mix

Easily confused with: AN-30, AN-23

## Fix and customer guidance
No error code will show this. Ask for request-ids by platform and date, and for which sessions or users are affected: sticky routing means the same sessions fail repeatedly while aggregate canaries look clean. Per-session (per-user) quality probes catch low-rate sticky faults that sampled canaries miss.

Sources: https://www.anthropic.com/engineering/a-postmortem-of-three-recent-issues

## Escalate when
Escalate to the provider/platform incident channel with request IDs, UTC window and blast radius once two or more customers or keys show it.

## Provenance
- rates_by_platform: doc-verbatim
- sticky_routing: doc-verbatim
- corruption_text: doc-partial
- detectors: synthesized

Reproduce: `python -m casebook repro SIM-3`
