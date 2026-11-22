# ID-02 — SAML assertion not yet valid / expired: IdP clock drift or a stale assertion

**Scope:** protocol behaviour (SAML / OAuth / OIDC), independent of any vendor.

Fix NTP on the IdP; don't replay or queue assertions; compare NotBefore/NotOnOrAfter with the SP clock.

Sources: https://docs.oasis-open.org/security/saml/v2.0/saml-core-2.0-os.pdf
