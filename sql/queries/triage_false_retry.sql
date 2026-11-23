-- How often each triage mode told a customer to retry a failure that retrying cannot fix, per model.
SELECT p.model, p.mode,
       COUNT(*)                                                   AS answers,
       SUM(p.retry = 'Y' AND t.gold_retry = 'N')                  AS false_retry,
       SUM(p.label <> t.gold AND p.label <> 'needs_more_info')    AS wrong,
       SUM(p.llm_error)                                           AS llm_errors,
       SUM(p.leaked_secret)                                       AS leaks
FROM predictions p JOIN tickets t ON t.id = p.ticket
GROUP BY p.model, p.mode
ORDER BY p.model, CASE p.mode WHEN 'rules' THEN 0 WHEN 'llm' THEN 1 ELSE 2 END;
