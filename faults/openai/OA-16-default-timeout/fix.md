# OA-16 — Long call killed at exactly 10 minutes (SDK default timeout)

**Retry:** C

Failures at exactly 600 s are the client's default timeout, not the API. Raise `timeout`, stream, or use background mode / Batches.

Sources: https://github.com/openai/openai-python, https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python
