# ID-07 — OIDC: key rotated and old key removed at once; strict issuer/audience

**Scope:** protocol behaviour (SAML / OAuth / OIDC), independent of any vendor.

Publish old and new keys during rotation; refetch JWKS on an unknown kid; match iss exactly.

Sources: https://openid.net/specs/openid-connect-core-1_0.html
