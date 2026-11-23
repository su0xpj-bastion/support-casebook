"""Three wire protocols behind one interface.

  ChatAdapter       OpenAI Chat Completions (OpenAI, Cerebras, Perplexity, Meta's OpenAI-compatible mode)
  ResponsesAdapter  OpenAI Responses API (OpenAI, Meta Model API)
  MessagesAdapter   Anthropic Messages API (Claude, Meta's Anthropic-compatible mode)

Each adapter keeps the conversation in its provider's native shape, so what goes over the
wire is exactly what that provider documents. Scenarios only see the normalized Result.
Every call streams, so time to first token is measured the same way everywhere.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import openai

STOPS = ("end", "length", "tool", "refusal", "other")


@dataclass
class ToolCall:
    id: str
    name: str
    raw_args: str
    args: dict | None             # None when raw_args is not valid JSON


@dataclass
class Result:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "other"           # normalized: end | length | tool | refusal | other
    raw_stop: str | None = None
    input_tokens: int | None = None          # all prompt tokens, cached or not
    output_tokens: int | None = None
    total_tokens: int | None = None          # only if the provider reports one
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    usage_seen: bool = False
    ttft: float | None = None
    wall: float = 0.0
    model: str | None = None


def _args(raw: str) -> dict | None:
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        return None


class Adapter:
    protocol = "base"

    def __init__(self, name: str, model: str, base_url: str, api_key: str, cfg: dict | None = None):
        self.name, self.model, self.base_url, self.api_key = name, model, base_url, api_key
        self.cfg = cfg or {}

    # conversation helpers: native message lists
    def new(self, system: str | None, user: str) -> dict:
        raise NotImplementedError

    def add_user(self, conv: dict, text: str) -> None:
        raise NotImplementedError

    def add_tool_results(self, conv: dict, result: Result, outputs: dict[str, str]) -> None:
        raise NotImplementedError

    def send(self, conv: dict, *, tools: list[dict] | None = None, schema: dict | None = None,
             max_tokens: int = 256, cache: bool = False, model: str | None = None, key: str | None = None) -> Result:
        raise NotImplementedError


# ------------------------------------------------------------------ Chat Completions

class ChatAdapter(Adapter):
    protocol = "chat"

    def client(self, key: str | None = None) -> openai.OpenAI:
        return openai.OpenAI(base_url=self.base_url, api_key=key or self.api_key, max_retries=0, timeout=120)

    def new(self, system, user):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        return {"messages": msgs}

    def add_user(self, conv, text):
        conv["messages"].append({"role": "user", "content": text})

    def add_tool_results(self, conv, result, outputs):
        for call in result.tool_calls:
            conv["messages"].append({"role": "tool", "tool_call_id": call.id, "content": outputs[call.id]})

    def send(self, conv, *, tools=None, schema=None, max_tokens=256, cache=False, model=None, key=None):
        kw: dict[str, Any] = {"model": model or self.model, "messages": conv["messages"], "max_tokens": max_tokens,
                              "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            kw["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                             "parameters": t["schema"], "strict": True}} for t in tools]
        if schema:
            kw["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}}
        if cache and self.cfg.get("prompt_cache_key"):
            kw["extra_body"] = {"prompt_cache_key": self.cfg["prompt_cache_key"]}
        r, calls, start = Result(), {}, time.perf_counter()
        with self.client(key).chat.completions.create(**kw) as stream:
            for chunk in stream:
                if chunk.choices:
                    d = chunk.choices[0].delta
                    if d.content:
                        r.ttft = r.ttft if r.ttft is not None else time.perf_counter() - start
                        r.text += d.content
                    for tc in d.tool_calls or []:
                        r.ttft = r.ttft if r.ttft is not None else time.perf_counter() - start
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.id or slot["id"]
                        if tc.function:
                            slot["name"] = tc.function.name or slot["name"]
                            slot["args"] += tc.function.arguments or ""
                    if chunk.choices[0].finish_reason:
                        r.raw_stop = chunk.choices[0].finish_reason
                if chunk.usage:
                    r.usage_seen = True
                    r.input_tokens, r.output_tokens = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                    r.total_tokens = chunk.usage.total_tokens
                    det = chunk.usage.prompt_tokens_details
                    r.cached_tokens = (det.cached_tokens or 0) if det else 0
                r.model = chunk.model or r.model
        r.wall = time.perf_counter() - start
        r.tool_calls = [ToolCall(c["id"], c["name"], c["args"], _args(c["args"])) for _, c in sorted(calls.items())]
        r.stop = {"stop": "end", "length": "length", "tool_calls": "tool", "content_filter": "refusal"}.get(r.raw_stop or "", "other")
        conv["messages"].append({"role": "assistant", "content": r.text or None,
                                 **({"tool_calls": [{"id": c.id, "type": "function", "function": {
                                     "name": c.name, "arguments": c.raw_args}} for c in r.tool_calls]} if r.tool_calls else {})})
        return r


# ------------------------------------------------------------------ Responses API

class ResponsesAdapter(Adapter):
    protocol = "responses"

    def client(self, key=None) -> openai.OpenAI:
        return openai.OpenAI(base_url=self.base_url, api_key=key or self.api_key, max_retries=0, timeout=120)

    def new(self, system, user):
        return {"instructions": system, "input": [{"role": "user", "content": user}]}

    def add_user(self, conv, text):
        conv["input"].append({"role": "user", "content": text})

    def add_tool_results(self, conv, result, outputs):
        for call in result.tool_calls:
            conv["input"].append({"type": "function_call_output", "call_id": call.id, "output": outputs[call.id]})

    def send(self, conv, *, tools=None, schema=None, max_tokens=256, cache=False, model=None, key=None):
        kw: dict[str, Any] = {"model": model or self.model, "input": conv["input"], "max_output_tokens": max_tokens,
                              "stream": True, "store": False}
        if conv.get("instructions"):
            kw["instructions"] = conv["instructions"]
        if self.cfg.get("reasoning_effort"):
            kw["reasoning"] = {"effort": self.cfg["reasoning_effort"]}
        if tools:
            kw["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                            "parameters": t["schema"], "strict": True} for t in tools]
        if schema:
            kw["text"] = {"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": True}}
        if cache and self.cfg.get("prompt_cache_key"):
            kw["prompt_cache_key"] = self.cfg["prompt_cache_key"]
        r, final, start = Result(), None, time.perf_counter()
        for event in self.client(key).responses.create(**kw):
            if event.type in ("response.output_text.delta", "response.function_call_arguments.delta") and r.ttft is None:
                r.ttft = time.perf_counter() - start
            if event.type in ("response.completed", "response.incomplete", "response.failed"):
                final = event.response
        r.wall = time.perf_counter() - start
        if final is None:
            raise RuntimeError("stream ended without a response.completed / incomplete event")
        r.model = final.model
        refused = False
        for item in final.output or []:
            if item.type == "message":
                for part in item.content or []:
                    if part.type == "output_text":
                        r.text += part.text
                    elif part.type == "refusal":
                        refused = True
            elif item.type == "function_call":
                r.tool_calls.append(ToolCall(item.call_id, item.name, item.arguments, _args(item.arguments)))
        if final.usage:
            r.usage_seen = True
            r.input_tokens, r.output_tokens = final.usage.input_tokens, final.usage.output_tokens
            r.total_tokens = final.usage.total_tokens
            det = final.usage.input_tokens_details
            r.cached_tokens = (det.cached_tokens or 0) if det else 0
        reason = final.incomplete_details.reason if final.incomplete_details else None
        r.raw_stop = reason or final.status
        r.stop = ("refusal" if refused else "length" if reason == "max_output_tokens" else "tool" if r.tool_calls
                  else "end" if final.status == "completed" else "other")
        # carry the model's output items forward, as the docs describe for stateless (store=False) use
        for item in final.output or []:
            if item.type == "message":
                conv["input"].append({"role": "assistant", "content": "".join(
                    p.text for p in item.content or [] if p.type == "output_text")})
            elif item.type == "function_call":
                conv["input"].append({"type": "function_call", "call_id": item.call_id, "name": item.name,
                                      "arguments": item.arguments})
        return r


# ------------------------------------------------------------------ Anthropic Messages

class MessagesAdapter(Adapter):
    protocol = "messages"

    def client(self, key=None) -> anthropic.Anthropic:
        return anthropic.Anthropic(base_url=self.base_url, api_key=key or self.api_key, max_retries=0, timeout=120)

    def new(self, system, user):
        return {"system": system, "messages": [{"role": "user", "content": user}]}

    def add_user(self, conv, text):
        conv["messages"].append({"role": "user", "content": text})

    def add_tool_results(self, conv, result, outputs):
        # every tool_result for one assistant turn goes back in ONE user message
        conv["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": outputs[c.id]} for c in result.tool_calls]})

    def send(self, conv, *, tools=None, schema=None, max_tokens=256, cache=False, model=None, key=None):
        kw: dict[str, Any] = {"model": model or self.model, "messages": conv["messages"], "max_tokens": max_tokens}
        if conv.get("system"):
            kw["system"] = conv["system"]
        if self.cfg.get("effort"):
            kw["output_config"] = {"effort": self.cfg["effort"]}
        if tools:
            kw["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"],
                            "strict": True} for t in tools]
        if schema:
            kw["output_config"] = {**kw.get("output_config", {}), "format": {"type": "json_schema", "schema": schema}}
        if cache:
            kw["cache_control"] = {"type": "ephemeral"}
        r, start = Result(), time.perf_counter()
        with self.client(key).messages.stream(**kw) as stream:
            for event in stream:
                if event.type == "content_block_delta" and r.ttft is None:
                    r.ttft = time.perf_counter() - start
            msg = stream.get_final_message()
        r.wall = time.perf_counter() - start
        r.model = msg.model
        for block in msg.content:
            if block.type == "text":
                r.text += block.text
            elif block.type == "tool_use":
                raw = json.dumps(block.input) if isinstance(block.input, dict) else str(block.input)
                r.tool_calls.append(ToolCall(block.id, block.name, raw, block.input if isinstance(block.input, dict) else None))
        u = msg.usage
        if u is not None and u.input_tokens is not None:
            r.usage_seen = True
            r.cached_tokens = u.cache_read_input_tokens or 0
            r.cache_write_tokens = u.cache_creation_input_tokens or 0
            r.input_tokens = u.input_tokens + r.cached_tokens + r.cache_write_tokens
            r.output_tokens = u.output_tokens
        r.raw_stop = msg.stop_reason
        r.stop = {"end_turn": "end", "stop_sequence": "end", "max_tokens": "length", "tool_use": "tool",
                  "refusal": "refusal"}.get(msg.stop_reason or "", "other")
        conv["messages"].append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
        return r


ADAPTERS = {"chat": ChatAdapter, "responses": ResponsesAdapter, "messages": MessagesAdapter}
API_ERRORS = (openai.APIError, anthropic.APIError)
