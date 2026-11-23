-- Where the LLM-alone triage goes wrong: wrong answers by error family and evidence level, per model.
SELECT p.model, t.family, t.evidence_level,
       COUNT(*)                                                 AS tickets,
       SUM(p.label <> t.gold AND p.label <> 'needs_more_info')  AS wrong
FROM predictions p JOIN tickets t ON t.id = p.ticket
WHERE p.mode = 'llm'
GROUP BY p.model, t.family, t.evidence_level
HAVING wrong > 0
ORDER BY p.model, wrong DESC;
