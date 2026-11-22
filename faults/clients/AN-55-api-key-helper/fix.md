# AN-55 — apiKeyHelper fails: request still sent with an empty bearer token

Fix the helper (exit 0, print the key on stdout); test it standalone. The docs' 'Your apiKeyHelper script is failing' text is not what 2.1.329 prints.

Sources: https://code.claude.com/docs/en/iam, https://code.claude.com/docs/en/settings
