-- Same HTTP status, different fix: statuses where the cases need different classes or retry decisions.
SELECT p.status,
       COUNT(*)                                        AS cases,
       COUNT(DISTINCT p.verdict)                       AS distinct_verdicts,
       GROUP_CONCAT(p.id || '=' || p.retry, ', ')      AS cases_and_retry
FROM (SELECT * FROM packs WHERE kind = 'error' AND status >= 400 ORDER BY id) AS p
GROUP BY p.status
HAVING COUNT(DISTINCT p.retry) > 1
ORDER BY cases DESC, p.status;
