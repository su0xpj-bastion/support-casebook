# AG-19 — 403 insufficient_scope vs 401 wrong audience

403: request the scopes in the WWW-Authenticate challenge (step-up). 401 with a valid-looking token: check the token's audience/resource against the server URL — the response looks identical to a missing token.

Sources: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
