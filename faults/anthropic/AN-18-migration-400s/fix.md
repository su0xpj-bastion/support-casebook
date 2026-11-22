# AN-18 — Migration 400s on newest models: prefill, thinking.enabled, sampling params, old computer tool

**Retry:** N

Prefill -> structured outputs (output_config.format) or a user turn; thinking.enabled -> adaptive + output_config.effort; drop temperature/top_p/top_k; computer tool -> computer_toolset_20260801.

Sources: https://platform.claude.com/docs/en/api/errors, https://platform.claude.com/docs/en/about-claude/model-deprecations
