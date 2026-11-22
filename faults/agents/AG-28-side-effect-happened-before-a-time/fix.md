# AG-28 — Side effect happened before a timeout; replaying the turn repeats it

Check the system of record first; never replay a turn blindly; dedupe on a business key (call_id alone changes on replay).

Sources: https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls, https://developers.openai.com/api/docs/guides/function-calling
