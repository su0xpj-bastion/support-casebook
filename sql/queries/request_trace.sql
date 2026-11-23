-- Every request one case sent, in order, with the request id the server returned and the SDK's retry count. Param: pack.
SELECT r.mode, r.seq, r.path, r.request_id, r.retry_count, r.client_request_id
FROM requests r
WHERE r.pack = :pack
ORDER BY r.mode, r.seq;
