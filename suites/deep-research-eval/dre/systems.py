"""Deep-research systems under test. Each returns an Answer for one question.

  mock        a seeded simulated research agent (CI); faults make it worse in specific ways
  perplexity  Perplexity Sonar via Chat Completions (search is built in; citations returned)
  openai      OpenAI Responses API with the hosted web_search tool
  anthropic   Claude Messages API with the web_search server tool

The answer type (single vs list) is never sent, as the dataset card requires.
"""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field

from .grader import split_items

INSTRUCTIONS = ("Research the question on the web. Be exhaustive when it asks for several items. "
                "End your reply with one line that starts with 'FINAL ANSWER:' followed by the answer. "
                "If there are several items, separate them with semicolons.")


@dataclass
class Answer:
    text: str
    citations: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    seconds: float = 0.0
    error: str | None = None


# ------------------------------------------------------------------ mock

MOCK_FAULTS = {
    "drops_items": "finds only about half of each list",
    "hallucinated_extras": "adds two plausible but wrong items to lists",
    "no_citations": "answers without citing sources",
    "format_drift": "stops writing the FINAL ANSWER line",
    "wrong_singles": "gets most single-answer questions wrong",
    "flaky": "different answers on every repeat",
}


class MockSystem:
    """Seeded simulated agent: ~70% fully correct, realistic partial lists, deterministic per question."""

    name = "mock"

    def __init__(self, faults: set[str] | None = None, seed: int = 7):
        self.faults, self.seed = faults or set(), seed

    def ask(self, item: dict, attempt: int = 0) -> Answer:
        salt = f"{self.seed}:{item['id']}" + (f":{attempt}:{time.time_ns()}" if "flaky" in self.faults else "")
        rng = random.Random(int(hashlib.sha256(salt.encode()).hexdigest(), 16))
        gold = item["answer"]
        if item["answer_type"].startswith("Single"):
            right = rng.random() < (0.15 if "wrong_singles" in self.faults else 0.75)
            final = gold if right else "Unknown entity " + str(rng.randint(10, 99))
        else:
            items = split_items(gold)
            keep_p = 0.5 if "drops_items" in self.faults else 0.93
            kept = [g for g in items if rng.random() < keep_p] or items[:1]
            if "hallucinated_extras" in self.faults:
                kept += [f"Fabricated item {rng.randint(100, 999)}", f"Unverified entry {rng.randint(100, 999)}"]
            elif rng.random() < 0.08:
                kept.append(f"Near miss {rng.randint(100, 999)}")
            final = "; ".join(kept)
        body = f"I searched several sources and cross-checked them.\n\n"
        text = body + (final if "format_drift" in self.faults else f"FINAL ANSWER: {final}")
        cites = [] if "no_citations" in self.faults else [f"https://example.test/source/{rng.randint(1, 500)}"
                                                         for _ in range(rng.randint(2, 5))]
        return Answer(text, cites, input_tokens=900 + len(item["problem"]) // 4, output_tokens=60 + len(final) // 4,
                      searches=rng.randint(2, 8), seconds=round(rng.uniform(8, 40), 1))


# ------------------------------------------------------------------ live systems

class PerplexitySystem:
    name = "perplexity"

    def __init__(self, api_key: str, model: str = "sonar-pro"):
        import openai
        self.client = openai.OpenAI(base_url="https://api.perplexity.ai", api_key=api_key, max_retries=2, timeout=600)
        self.model = model

    def ask(self, item: dict, attempt: int = 0) -> Answer:
        start = time.perf_counter()
        r = self.client.chat.completions.create(model=self.model, messages=[
            {"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": item["problem"]}])
        extra = r.model_extra or {}
        cites = list(extra.get("citations") or [s.get("url") for s in extra.get("search_results") or []])
        return Answer(r.choices[0].message.content or "", cites, r.usage.prompt_tokens, r.usage.completion_tokens,
                      seconds=time.perf_counter() - start)


class OpenAISystem:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-6.1-sol"):
        import openai
        self.client = openai.OpenAI(api_key=api_key, max_retries=2, timeout=900)
        self.model = model

    def ask(self, item: dict, attempt: int = 0) -> Answer:
        start = time.perf_counter()
        r = self.client.responses.create(model=self.model, instructions=INSTRUCTIONS, input=item["problem"],
                                         tools=[{"type": "web_search"}], store=False)
        cites, searches = [], 0
        for out in r.output or []:
            if out.type == "web_search_call":
                searches += 1
            if out.type == "message":
                for part in out.content or []:
                    for ann in getattr(part, "annotations", None) or []:
                        if getattr(ann, "url", None):
                            cites.append(ann.url)
        return Answer(r.output_text or "", sorted(set(cites)), r.usage.input_tokens, r.usage.output_tokens, searches,
                      time.perf_counter() - start)


class AnthropicSystem:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=900)
        self.model = model

    def ask(self, item: dict, attempt: int = 0) -> Answer:
        start = time.perf_counter()
        messages = [{"role": "user", "content": item["problem"]}]
        text, cites, searches, tin, tout = "", set(), 0, 0, 0
        for _ in range(6):                               # continue server-tool turns that pause
            with self.client.messages.stream(model=self.model, max_tokens=16000, system=INSTRUCTIONS,
                                             messages=messages,
                                             tools=[{"type": "web_search_20260209", "name": "web_search"}]) as s:
                msg = s.get_final_message()
            tin += msg.usage.input_tokens + (msg.usage.cache_read_input_tokens or 0)
            tout += msg.usage.output_tokens
            for block in msg.content:
                if block.type == "text":
                    text += block.text
                    for c in block.citations or []:
                        if getattr(c, "url", None):
                            cites.add(c.url)
                elif block.type == "server_tool_use":
                    searches += 1
            if msg.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason == "refusal":
            return Answer(text, sorted(cites), tin, tout, searches, time.perf_counter() - start, error="refusal")
        return Answer(text, sorted(cites), tin, tout, searches, time.perf_counter() - start)
