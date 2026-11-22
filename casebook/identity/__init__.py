"""Identity lab: a stand-in IdP, a service provider *modelled on* the documented Claude
SSO/provisioning rules, a SCIM directory, an OAuth/OIDC authorization server and an OpenAI-style
Workload Identity Federation exchange.

What is real: XML signatures (signxml), JWT/JWKS verification (PyJWT), X.509 certificates
(cryptography). What is modelled: the policy decisions (domain verification, Require SSO, JIT vs
SCIM, group mappings, WIF mapping rules), written from the vendors' docs. The SP is NOT Anthropic's
system and its error codes are casebook's own; Docker is unavailable here, so no Keycloak.
"""
