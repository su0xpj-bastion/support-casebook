"""Measure the deterministic grader against an LLM judge.

  python -m dre.judge reports/latest/results.json --judge anthropic|openai|mock

DeepSearchQA is officially judged by an LLM with the gold answer. The deterministic grader
is free and reproducible but can miss paraphrases. This re-judges a finished run and
reports how often the two agree (raw agreement and Cohen's kappa) and lists every
disagreement, so the grader's error rate is a measured number.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .grader import tokens

JUDGE_PROMPT = """You are grading an answer to a research question against the gold answer.
Question: {question}
Gold answer: {gold}
Answer type: {kind}
Response (graded on its final answer): {response}

For a single answer: correct if it names the same entity or value as the gold answer (ignore formatting).
For a list: correct only if it contains every gold item and no wrong items (equivalent names count).
Reply with JSON only: {{"correct": true or false, "reason": "<one sentence>"}}"""


class MockJudge:
    """Lenient token-overlap judge, used to test the agreement plumbing offline."""

    def judge(self, question: str, gold: str, kind: str, response: str) -> bool:
        final = response.split("FINAL ANSWER:")[-1]
        g, p = tokens(gold), tokens(final)
        return bool(g) and len(g & p) / len(g) >= 0.9 and len(p - g) <= max(2, len(g) // 2)


class AnthropicJudge:
    def __init__(self, key: str, model: str = "claude-opus-5-5"):
        import anthropic
        self.client, self.model = anthropic.Anthropic(api_key=key, max_retries=2), model

    def judge(self, question, gold, kind, response) -> bool:
        msg = self.client.messages.create(
            model=self.model, max_tokens=1024, output_config={"effort": "low", "format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["correct", "reason"], "additionalProperties": False}}},
            messages=[{"role": "user", "content": JUDGE_PROMPT.format(question=question, gold=gold, kind=kind, response=response)}])
        text = next((b.text for b in msg.content if b.type == "text"), "{}")
        return bool(json.loads(text).get("correct"))


class OpenAIJudge:
    def __init__(self, key: str, model: str = "gpt-6.1-sol"):
        import openai
        self.client, self.model = openai.OpenAI(api_key=key, max_retries=2), model

    def judge(self, question, gold, kind, response) -> bool:
        r = self.client.responses.create(model=self.model, input=JUDGE_PROMPT.format(
            question=question, gold=gold, kind=kind, response=response), store=False,
            text={"format": {"type": "json_schema", "name": "verdict", "strict": True, "schema": {
                "type": "object", "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["correct", "reason"], "additionalProperties": False}}})
        return bool(json.loads(r.output_text).get("correct"))


def kappa(pairs: list[tuple[bool, bool]]) -> float:
    n = len(pairs)
    if not n:
        return 0.0
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def main() -> int:
    ap = argparse.ArgumentParser(prog="dre.judge")
    ap.add_argument("results")
    ap.add_argument("--judge", default="mock", choices=["mock", "anthropic", "openai"])
    a = ap.parse_args()
    data = json.loads(Path(a.results).read_text())
    problems = {json.loads(l)["id"]: json.loads(l) for l in (Path(__file__).resolve().parent.parent / "data" / "dsqa_slice.jsonl").read_text().splitlines()}
    if a.judge == "mock":
        judge = MockJudge()
    else:
        env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[a.judge]
        if not os.environ.get(env):
            print(f"[ERROR] {env} is not set", file=sys.stderr)
            return 2
        judge = (AnthropicJudge if a.judge == "anthropic" else OpenAIJudge)(os.environ[env])
    pairs, diffs = [], []
    for row in (r for r in data["rows"] if r["attempt"] == 0):
        item = problems[row["id"]]
        j = judge.judge(item["problem"], item["answer"], item["answer_type"], row["answer"]["text"])
        d = bool(row["grade"]["correct"])
        pairs.append((d, j))
        if d != j:
            diffs.append(f"- `{row['id']}` grader={'correct' if d else 'wrong'}, judge={'correct' if j else 'wrong'}: "
                         f"gold {item['answer'][:70]!r} / answer {re.sub(r'\\s+', ' ', row['answer']['text'].split('FINAL ANSWER:')[-1])[:70]!r}")
    agree = sum(a == b for a, b in pairs) / len(pairs)
    print(f"# Grader vs {a.judge} judge\n\n{len(pairs)} answers · agreement {agree:.0%} · Cohen's kappa {kappa(pairs):.2f}\n")
    print("\n".join(diffs) or "No disagreements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
