# ID-04 — IdP rotated its signing certificate; SP still trusts the old one

**Scope:** protocol behaviour (SAML / OAuth / OIDC), independent of any vendor.

Upload the new IdP certificate in the SP ('Manage SSO' → metadata) and run Test sign-in; rotate on both sides together.

Sources: https://docs.oasis-open.org/security/saml/v2.0/saml-core-2.0-os.pdf, https://support.claude.com/en/articles/13132885-setting-up-single-sign-on-sso
