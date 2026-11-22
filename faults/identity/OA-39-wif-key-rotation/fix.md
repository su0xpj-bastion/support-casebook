# OA-39 — WIF fails after key rotation (uploaded JWKS stale / old key removed early)

**Scope:** vendor policy, modelled from the docs cited in `pack.yaml`.

Upload the new JWKS before issuing new-kid tokens; publish both keys during rotation.

Sources: https://developers.openai.com/api/docs/guides/workload-identity-federation
