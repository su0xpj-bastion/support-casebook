# AN-45 — Managed forceLoginOrgUUID blocks an env API key at startup

Unset ANTHROPIC_API_KEY and run `claude auth login`; admins keep forceLoginOrgUUID only where the admin console manages the machine.

Sources: https://code.claude.com/docs/en/iam, https://code.claude.com/docs/en/settings
