# AN-09 — Stream dies after HTTP 200 with event: error (overloaded)

**Retry:** C

Keep the partial output; retry the turn (on 4.6+ models you can continue via a user message). Status-code retry logic never sees this error — handle the stream error event.

Sources: https://platform.claude.com/docs/en/build-with-claude/streaming, https://platform.claude.com/docs/en/api/errors
