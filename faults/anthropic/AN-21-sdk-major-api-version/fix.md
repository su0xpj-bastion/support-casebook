# AN-21 — SDK major upgrade changes anthropic-version: sampling params now 400

**Retry:** N

Drop the sampling params (preferred) or pin anthropic-version until migrated. On Bedrock, models <=4.6 don't support 2026-10-21: pin the SDK (anthropic[bedrock]<2).

Sources: https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python, https://platform.claude.com/docs/en/about-claude/model-deprecations
