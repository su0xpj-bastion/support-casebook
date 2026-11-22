# AN-23 — Refusal reported as an outage (HTTP 200, stop_reason=refusal)

**Retry:** N

Not an outage: handle stop_reason=refusal in code (fallback or rephrase); escalate only with request-ids if refusals look wrong.

Sources: https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
