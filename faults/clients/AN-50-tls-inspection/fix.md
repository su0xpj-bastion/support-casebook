# AN-50 — Corporate TLS inspection: SELF_SIGNED_CERT_IN_CHAIN; NODE_EXTRA_CA_CERTS fixes it

Install the inspection root CA via NODE_EXTRA_CA_CERTS (a PEM path that exists — a wrong path is only a warning).

Sources: https://code.claude.com/docs/en/network-config
