-- Requests the SDK's default retries sent for failures that retrying cannot fix (retry = N), versus the evidence-aware policy.
SELECT d.pack, k.status, k.error_code,
       d.requests_sent                     AS sdk_default_requests,
       p.requests_sent                     AS policy_requests,
       d.requests_sent - p.requests_sent   AS wasted
FROM repro_runs d
JOIN repro_runs p ON p.pack = d.pack AND p.run = d.run AND p.mode = 'policy'
JOIN packs k      ON k.id = d.pack
WHERE d.mode = 'sdk-default' AND k.retry = 'N' AND d.requests_sent > p.requests_sent
ORDER BY wasted DESC, d.pack;
