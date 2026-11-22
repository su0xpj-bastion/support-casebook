# SIM-1 — Provider outage — classify 'platform-side' from client signals before the status page

*Provider:* openai · *surface:* api · *kind:* sim · *retry:* - · *weakest provenance:* `synthesized`

## Confirm it
- error mix across clients
- endpoints affected
- status page state
- one customer-side control

Easily confused with: OA-10, OA-14, network.connection

## Fix and customer guidance
Is it us or them? Connection-level failures across many keys and more than one endpoint, with no 4xx, point at the provider; one key failing with 401 points at the customer. Post the first customer update as soon as the classification is made — before the status page if the evidence is clear — with explicit unknowns and a next-update time.

Sources: https://status.openai.com/

## Escalate when
Escalate to the provider/platform incident channel with request IDs, UTC window and blast radius once two or more customers or keys show it.

## Provenance
- incident_shape: doc-partial
- timings: synthesized
- status_post_at_plus_1: doc-verbatim

Reproduce: `python -m casebook repro SIM-1`
