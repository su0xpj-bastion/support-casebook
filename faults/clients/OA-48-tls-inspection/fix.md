# OA-48 — Codex behind TLS inspection: 'Reconnecting... waiting for network' forever

Set CODEX_CA_CERTIFICATE (or SSL_CERT_FILE) to the inspection root CA; the reconnect loop is a certificate problem, not the network.

Sources: https://learn.chatgpt.com/docs/auth
