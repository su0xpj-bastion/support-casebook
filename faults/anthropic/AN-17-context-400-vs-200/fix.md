# AN-17 — Context: 400 'prompt is too long' vs 200 model_context_window_exceeded

**Retry:** N

400: shrink input (count tokens first). 200 with model_context_window_exceeded: input fit, output was cut — lower max_tokens or compact context.

Sources: https://platform.claude.com/docs/en/build-with-claude/context-windows, https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
