### Your retry settings are extending the outage — P1 — update at 2026-04-20 16:00 UTC

**Confirmed facts**
- Measured on the same incident: unbounded immediate retries sent 9.95x the requests and did not recover after capacity returned
- A jittered backoff with a retry budget sent 1.04x and recovered first

**Customer impact**
While the platform is degraded, your client fleet's retries keep the endpoint saturated for you and others.

**Current mitigation**
Cap retries (backoff with jitter, at most ~10% extra traffic), or rely on the SDK defaults.

**Owner**
Support (casebook)

**Next update**
2026-04-20 16:30 UTC

**Explicit unknowns**
- platform ETA
