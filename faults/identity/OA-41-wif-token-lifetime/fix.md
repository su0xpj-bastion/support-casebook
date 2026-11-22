# OA-41 — WIF clients fail at about 1 h: no refresh token

**Scope:** vendor policy, modelled from the docs cited in `pack.yaml`.

Re-exchange a fresh subject token before expires_at.

Sources: https://developers.openai.com/api/docs/guides/workload-identity-federation
