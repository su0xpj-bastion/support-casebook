### API requests failing across all keys — P1 — update at 2024-12-11 23:48 UTC

**Confirmed facts**
- Connection failures on chat completions and model listing from 10 independent clients since 23:43 UTC; no 4xx except one unrelated revoked key
- Failures are connection-level (no HTTP response), consistent with a platform-side incident

**Customer impact**
All API calls and dashboard sign-in may fail; requests in flight may not complete.

**Current mitigation**
We have escalated to the platform incident channel and are tracking the status page.

**Owner**
Support incident commander (casebook)

**Next update**
2024-12-12 00:18 UTC

**Explicit unknowns**
- root cause
- time to recovery
