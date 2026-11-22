# AN-11 — 504 timeout_error; SDK retries create several request-ids per logical call

**Retry:** C

Stream long requests (or use Batches); give support every request-id for the logical call. Blind retries of long non-streaming calls repeat the work.

Sources: https://platform.claude.com/docs/en/api/errors
