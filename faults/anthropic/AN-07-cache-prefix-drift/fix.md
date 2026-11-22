# AN-07 — Caching broke after deploy: tool order changed

**Retry:** -

Serialize tools in a stable order (and stable JSON key order). The mock shows a write instead of a read on the drifted call, then a read once the order is stable.

Sources: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
