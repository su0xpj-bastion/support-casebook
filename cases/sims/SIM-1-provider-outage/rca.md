# RCA: SIM-1 provider outage (template: OpenAI 2024-12-11)

## Summary
A platform dependency failed and every API call and dashboard sign-in failed at the connection level for one hour.

## Impact
All keys and endpoints; no requests completed during the window.

## Root cause
Platform-side (in the template incident: a telemetry deployment overloaded the Kubernetes control plane, breaking DNS-based service discovery).

Evidence:
- provider-side verdict 5 min after impact from client packets
- no 4xx except one revoked key (control)
- timeline.jsonl in this folder

## Mitigation and recovery
- customer updates every 30 min with explicit unknowns
- recovery confirmed from client signals 10 min after it happened

## Prevention and follow-up
- alert on cross-client connection-error ratio, not per-key error counts
- pre-written P1 update template
