-- Responses captured from the real APIs: how many expected fields matched, and whether the server said to retry.
SELECT c.pack, c.captured_utc, c.status, c.x_should_retry, c.fields_matched || '/' || c.fields_total AS matched, c.verdict
FROM live_captures c
ORDER BY c.pack;
