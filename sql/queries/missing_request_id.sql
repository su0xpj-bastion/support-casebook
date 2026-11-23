-- Failures that leave the customer no provider request id to quote, so support must ask for other evidence.
SELECT r.pack, k.title, r.exception, r.verdict
FROM repro_runs r JOIN packs k ON k.id = r.pack
WHERE r.mode = 'sdk-default' AND r.provider_request_id IS NULL
ORDER BY r.pack;
