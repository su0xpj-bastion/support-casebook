## Escalation: Connection failures across all keys and endpoints

- **Verdict:** sim.provider_outage (retryable=C, owner=provider, basis: connection errors on >=2 endpoints from >=60% of clients)
- **Window:** 2024-12-11 23:43 UTC → 2024-12-12 00:43 UTC
- **Request IDs:** provider=None client=claims-svc-7f3a-0001 cloud=None
- **Org / project-workspace:** org-casebook0001 / None
- **Error:** status=None type=connection_error code=None
- **SDK:** {'name': 'openai-python', 'version': '3.51.0'}
- **Blast radius:** 10 of 10 monitored clients; chat completions and model listing
- **Repro:** `python -m casebook repro SIM-1`
- **Ask:** Confirm incident and ETA; is login (dashboard) on the same dependency?
