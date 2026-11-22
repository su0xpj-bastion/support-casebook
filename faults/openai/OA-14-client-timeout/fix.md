# OA-14 — Client timeout with no x-request-id — and SDK retries resend the work

**Retry:** C

Give support the X-Client-Request-Id, UTC timestamp and org/project. Each SDK retry after a timeout sends the request again; the server may still process every copy. Raise the timeout or use background mode for long calls.

Sources: https://developers.openai.com/api/reference/overview, https://github.com/openai/openai-python
