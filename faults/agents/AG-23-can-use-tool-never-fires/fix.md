# AG-23 — can_use_tool never fires: an allow rule approves the tool first

Remove the tool from allowed_tools if the callback must decide; the SDK warns about this shadowing.

Sources: https://code.claude.com/docs/en/agent-sdk/permissions
