# EvalPlus: graders that pass non-solutions

**Reported upstream:** [evalplus/evalplus#333](https://github.com/evalplus/evalplus/issues/333), [#334](https://github.com/evalplus/evalplus/issues/334), [#335](https://github.com/evalplus/evalplus/issues/335), [#336](https://github.com/evalplus/evalplus/issues/336) — filed 2026-11-17, all open on 2026-11-21.
**Source project:** [evalaudit](https://github.com/su0xpj-bastion/evalaudit) (full write-up and reproduction).

## Symptom (as a customer would report it)
"Our model's pass@1 on HumanEval+ jumped to 99% — can we trust it?"

## Evidence
- A solution that never solves anything scores **pass@1 0.994 on HumanEval+ and 1.000 on MBPP+** by reading `expected` from the grader's frame or overriding `__eq__` (#333).
- `HumanEval/32`'s reference fails the `find_zero` oracle on 6 plus inputs, and in 0.3.1 every solution fails it (#334).
- Per-input timeouts are reported as wrong answers, so verdicts depend on machine load (#335).
- `Mbpp/793` has an empty plus suite; 6 MBPP+ plus suites are passed by a constant function (#336).

## Escalation as written
Minimal repro per issue, expected vs actual, the exact grader code path, and a proposed fix (the hardened grader in evalaudit lets 0 of 542 cheating solutions through).

## Support lesson
A headline metric that moves suspiciously is a ticket about the measurement before it's a ticket about the model. Ask for the grader version and a failing-on-purpose control.
