"""One mock that speaks three wire protocols:

  POST /v1/chat/completions   OpenAI Chat Completions (SSE, include_usage chunk)
  POST /v1/responses          OpenAI Responses API (typed SSE events, response.completed / incomplete)
  POST /v1/messages           Anthropic Messages (message_start ... message_stop)

Requests are converted to one neutral conversation, a small rule-based "brain" decides the
reply (text, tool calls, or JSON), and the reply is encoded back in the caller's protocol.
Faults (--faults / MOCK_FAULTS) break one behaviour for every protocol:

  usage_drift          total_tokens != input + output (where a total is reported)
  stream_no_usage      the stream carries no usage
  ignore_max_tokens    the output limit is ignored
  stop_reason_wrong    a length cut is reported as a normal stop
  bad_tool_args        tool-call arguments are not valid JSON
  schema_violation     structured output misses a required field
  tool_result_ignored  the reply after a tool result ignores it
  injection_follows    instructions inside a tool result are obeyed
  history_dropped      only the latest user message is seen
  cache_never_hits     the prompt cache never reports a hit
  handoff_misroute     the triage agent sends everything to billing
  error_shape          an unknown model returns 500 instead of 404
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {"mock-model", "mock-model-large"}
CACHE_MIN_TOKENS = 1024
TOKEN_DELAY = 0.002
CITIES = {"Paris": (18, "sunny"), "Tokyo": (22, "cloudy"), "Lisbon": (21, "sunny"), "London": (12, "rain")}


def ntok(text: str) -> int:
    return max(1, len(text) // 4)


# ------------------------------------------------------------------ neutral conversation + brain

class Turn:
    def __init__(self, role: str, text: str = "", calls: list | None = None, results: list | None = None):
        self.role, self.text, self.calls, self.results = role, text, calls or [], results or []


def brain(system: str, turns: list[Turn], tools: list[str], schema: dict | None, faults: set[str]) -> dict:
    """Return {"text": str} | {"calls": [(name, args_dict)]}."""
    users = [t for t in turns if t.role == "user" and t.text]
    last = turns[-1] if turns else Turn("user")
    if "history_dropped" in faults and users:
        turns = [users[-1]]
    seen = " ".join(t.text for t in turns if t.role in ("user", "assistant"))

    if last.role == "tool":                                            # replying to tool results
        if "tool_result_ignored" in faults:
            return {"text": "I could not find that information."}
        parts = []
        for name, output in last.results:
            if "INJECTED" in output:
                if "injection_follows" in faults and "delete_all_files" in tools and not any(
                        c[0] == "delete_all_files" for t in turns for c in t.calls):
                    return {"calls": [("delete_all_files", {"confirm": True})]}
                parts.append("The document describes the quarterly release checklist.")
            elif "error" in output.lower():
                parts.append("The weather service is unavailable right now, so I can't check the weather.")
            else:
                try:
                    d = json.loads(output)
                    parts.append(f"It is {d['temp_c']}°C and {d['conditions']} in {d['city']}.")
                except (json.JSONDecodeError, KeyError, TypeError):
                    parts.append(f"The tool returned: {output[:60]}")
        return {"text": " ".join(parts)}

    q = last.text
    if any(t.startswith("transfer_to_") for t in tools):
        dept = "billing"
        if "handoff_misroute" not in faults:
            if re.search(r"log ?in|password|email on my account|two-factor", q, re.I):
                dept = "account"
            elif re.search(r"error|crash|502|timeout|bug", q, re.I):
                dept = "tech"
        return {"calls": [(f"transfer_to_{dept}", {"reason": q[:80]})]}
    if "read_doc" in tools and "doc at" in q:
        url = re.search(r"https?://\S+", q).group(0).rstrip(".")
        return {"calls": [("read_doc", {"url": url})]}
    if "get_weather" in tools and "weather" in q.lower():
        cities = [c for c in CITIES if c in q]
        return {"calls": [("get_weather", {"city": c}) for c in cities]}
    if schema:
        obj = {"city": "Lisbon", "temp_c": 21, "conditions": "sunny"}
        if "schema_violation" in faults:
            obj.pop("temp_c")
        return {"text": json.dumps(obj)}
    m = re.search(r"Reply with exactly: (\S+)", q)
    if m:
        return {"text": m.group(1)}
    if "code word?" in q:
        m = re.search(r"code word is ([A-Z]+-\d+)", seen)
        return {"text": f"Your code word is {m.group(1)}." if m else "I don't know your code word."}
    if "specialist" in system:
        return {"text": f"{system.split()[3].capitalize()} team here: I can help with that."}
    if "story" in q.lower():
        return {"text": " ".join(["Once"] + ["upon a time the river ran past the old mill"] * 40)}
    if "Reply OK" in q:
        return {"text": "OK"}
    return {"text": "Done."}


# ------------------------------------------------------------------ server

class State:
    def __init__(self, faults: set[str]):
        self.faults = faults
        self.cache: set[str] = set()
        self.lock = threading.Lock()

    def cached(self, prefix: str, enabled: bool) -> int:
        if not enabled or ntok(prefix) < CACHE_MIN_TOKENS:
            return 0
        key = hashlib.sha1(prefix.encode()).hexdigest()
        with self.lock:
            hit = key in self.cache
            self.cache.add(key)
        return ntok(prefix) if hit and "cache_never_hits" not in self.faults else 0


class Handler(BaseHTTPRequestHandler):
    state: State
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- plumbing ----
    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _sse(self, data, event=None):
        payload = (f"event: {event}\n" if event else "") + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
        raw = payload.encode()
        self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
        self.wfile.flush()

    def _sse_end(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _error(self, protocol, code, kind, message):
        if protocol == "messages":
            return self._json(code, {"type": "error", "error": {"type": kind, "message": message}})
        return self._json(code, {"error": {"message": message, "type": kind, "code": kind}})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        protocol = {"/v1/chat/completions": "chat", "/v1/responses": "responses", "/v1/messages": "messages"}.get(path)
        if not protocol:
            return self._json(404, {"error": {"message": "not found"}})
        key = self.headers.get("x-api-key") or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if key == "bad-key":
            kind = "authentication_error" if protocol == "messages" else "invalid_api_key"
            return self._error(protocol, 401, kind, "invalid x-api-key" if protocol == "messages" else "Incorrect API key provided")
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if req.get("model") not in MODELS:
            if "error_shape" in self.state.faults:
                return self._json(500, {"oops": "internal"})
            kind = "not_found_error" if protocol == "messages" else "model_not_found"
            return self._error(protocol, 404, kind, f"model: {req.get('model')} not found")
        limit = next((req[k] for k in ("max_tokens", "max_completion_tokens", "max_output_tokens") if k in req), None)
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return self._error(protocol, 400, "invalid_request_error", "max_tokens: must be >= 1")
        getattr(self, f"_{protocol}")(req, int(limit or 4096))

    # ---- shared generation ----
    def _generate(self, system, turns, tools, schema, limit, cache_prefix, cache_on):
        out = brain(system or "", turns, tools, schema, self.state.faults)
        prompt_text = (system or "") + " ".join(t.text + " ".join(r[1] for r in t.results) for t in turns)
        inp = ntok(prompt_text)
        cached = self.state.cached(cache_prefix, cache_on)
        if "calls" in out:
            calls = []
            for name, args in out["calls"]:
                raw = json.dumps(args)
                if "bad_tool_args" in self.state.faults:
                    raw = raw[:-3]
                calls.append((f"call_{uuid.uuid4().hex[:10]}", name, raw))
            return {"calls": calls, "stop": "tool", "input": inp, "output": sum(ntok(c[2]) for c in calls) + 5, "cached": cached}
        words = out["text"].split(" ")
        pieces = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]
        stop = "end"
        if len(pieces) > limit and "ignore_max_tokens" not in self.state.faults:
            pieces, stop = pieces[:limit], "length"
        if stop == "length" and "stop_reason_wrong" in self.state.faults:
            stop = "end"
        return {"pieces": pieces, "stop": stop, "input": inp, "output": len(pieces), "cached": cached}

    # ---- Chat Completions ----
    def _chat(self, req, limit):
        system, turns = "", []
        for m in req.get("messages", []):
            if m["role"] == "system":
                system += m["content"]
            elif m["role"] == "user":
                turns.append(Turn("user", m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])))
            elif m["role"] == "assistant":
                turns.append(Turn("assistant", m.get("content") or "", [(c["function"]["name"], c["id"]) for c in m.get("tool_calls") or []]))
            elif m["role"] == "tool":
                if not turns or turns[-1].role != "tool":
                    turns.append(Turn("tool"))
                turns[-1].results.append(("", m["content"]))
        tools = [t["function"]["name"] for t in req.get("tools") or []]
        schema = (req.get("response_format") or {}).get("json_schema", {}).get("schema")
        g = self._generate(system, turns, tools, schema, limit, system, True)
        usage = {"prompt_tokens": g["input"], "completion_tokens": g["output"],
                 "total_tokens": g["input"] + g["output"] + (9 if "usage_drift" in self.state.faults else 0),
                 "prompt_tokens_details": {"cached_tokens": g["cached"]}}
        base = {"id": f"chatcmpl-{uuid.uuid4().hex[:8]}", "object": "chat.completion.chunk", "created": int(time.time()),
                "model": req["model"]}
        self._sse_start()
        self._sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        if "calls" in g:
            for i, (cid, name, raw) in enumerate(g["calls"]):
                self._sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": cid, "type": "function",
                           "function": {"name": name, "arguments": ""}}]}, "finish_reason": None}]})
                self._sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": raw}}]},
                           "finish_reason": None}]})
        else:
            for p in g["pieces"]:
                time.sleep(TOKEN_DELAY)
                self._sse({**base, "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]})
        finish = {"end": "stop", "length": "length", "tool": "tool_calls"}[g["stop"]]
        self._sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        if (req.get("stream_options") or {}).get("include_usage") and "stream_no_usage" not in self.state.faults:
            self._sse({**base, "choices": [], "usage": usage})
        self._sse("[DONE]")
        self._sse_end()

    # ---- Responses ----
    def _responses(self, req, limit):
        system, turns = req.get("instructions") or "", []
        items = req.get("input")
        if isinstance(items, str):
            items = [{"role": "user", "content": items}]
        for it in items:
            if it.get("type") == "function_call_output":
                if not turns or turns[-1].role != "tool":
                    turns.append(Turn("tool"))
                turns[-1].results.append(("", it["output"]))
            elif it.get("type") == "function_call":
                turns.append(Turn("assistant", "", [(it["name"], it["call_id"])]))
            elif it.get("role") in ("user", "assistant", "system", "developer"):
                c = it["content"] if isinstance(it["content"], str) else " ".join(p.get("text", "") for p in it["content"])
                if it["role"] in ("system", "developer"):
                    system += c
                else:
                    turns.append(Turn(it["role"], c))
        tools = [t["name"] for t in req.get("tools") or [] if t.get("type") == "function"]
        fmt = ((req.get("text") or {}).get("format") or {})
        schema = fmt.get("schema") if fmt.get("type") == "json_schema" else None
        g = self._generate(system, turns, tools, schema, limit, system, True)
        rid = f"resp_{uuid.uuid4().hex[:12]}"
        output, seq = [], 0

        def ev(kind, **data):
            nonlocal seq
            seq += 1
            self._sse({"type": kind, "sequence_number": seq, **data}, event=kind)

        base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": req["model"],
                "status": "in_progress", "output": [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "incomplete_details": None, "error": None, "usage": None}
        self._sse_start()
        ev("response.created", response=base)
        if "calls" in g:
            for cid, name, raw in g["calls"]:
                item = {"type": "function_call", "id": f"fc_{cid}", "call_id": cid, "name": name, "arguments": raw, "status": "completed"}
                ev("response.output_item.added", output_index=len(output), item={**item, "arguments": ""})
                ev("response.function_call_arguments.delta", item_id=item["id"], output_index=len(output), delta=raw)
                ev("response.output_item.done", output_index=len(output), item=item)
                output.append(item)
        else:
            mid = f"msg_{uuid.uuid4().hex[:10]}"
            ev("response.output_item.added", output_index=0, item={"type": "message", "id": mid, "role": "assistant",
                                                                   "status": "in_progress", "content": []})
            for p in g["pieces"]:
                time.sleep(TOKEN_DELAY)
                ev("response.output_text.delta", item_id=mid, output_index=0, content_index=0, delta=p, logprobs=[])
            text = "".join(g["pieces"])
            item = {"type": "message", "id": mid, "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
            ev("response.output_item.done", output_index=0, item=item)
            output.append(item)
        usage = None if "stream_no_usage" in self.state.faults else {
            "input_tokens": g["input"], "input_tokens_details": {"cached_tokens": g["cached"]},
            "output_tokens": g["output"], "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": g["input"] + g["output"] + (9 if "usage_drift" in self.state.faults else 0)}
        done = g["stop"] == "length"
        final = {**base, "status": "incomplete" if done else "completed", "output": output, "usage": usage,
                 "incomplete_details": {"reason": "max_output_tokens"} if done else None}
        ev("response.incomplete" if done else "response.completed", response=final)
        self._sse_end()

    # ---- Anthropic Messages ----
    def _messages(self, req, limit):
        system = req.get("system") or ""
        if isinstance(system, list):
            system = " ".join(b.get("text", "") for b in system)
        turns = []
        for m in req.get("messages", []):
            content = m["content"]
            if isinstance(content, str):
                turns.append(Turn(m["role"], content))
                continue
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            uses = [(b["name"], b["id"]) for b in content if b.get("type") == "tool_use"]
            results = [("", b["content"] if isinstance(b["content"], str) else json.dumps(b["content"]))
                       for b in content if b.get("type") == "tool_result"]
            if results:
                turns.append(Turn("tool", results=results))
            else:
                turns.append(Turn(m["role"], " ".join(texts), uses))
        tools = [t["name"] for t in req.get("tools") or []]
        schema = ((req.get("output_config") or {}).get("format") or {}).get("schema")
        cache_on = bool(req.get("cache_control"))
        g = self._generate(system, turns, tools, schema, limit, system, cache_on)
        cached = g["cached"]
        write = 0 if cached or not cache_on or ntok(system) < CACHE_MIN_TOKENS else ntok(system)
        uncached = g["input"] - cached - write
        mid = f"msg_{uuid.uuid4().hex[:12]}"
        self._sse_start()
        start_usage = {"input_tokens": uncached, "output_tokens": 1, "cache_read_input_tokens": cached,
                       "cache_creation_input_tokens": write}
        self._sse({"type": "message_start", "message": {"id": mid, "type": "message", "role": "assistant", "content": [],
                   "model": req["model"], "stop_reason": None, "stop_sequence": None,
                   "usage": start_usage if "stream_no_usage" not in self.state.faults else
                   {"input_tokens": None, "output_tokens": None}}}, event="message_start")
        if "calls" in g:
            for i, (cid, name, raw) in enumerate(g["calls"]):
                self._sse({"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": cid,
                           "name": name, "input": {}}}, event="content_block_start")
                self._sse({"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta",
                           "partial_json": raw}}, event="content_block_delta")
                self._sse({"type": "content_block_stop", "index": i}, event="content_block_stop")
        else:
            self._sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      event="content_block_start")
            for p in g["pieces"]:
                time.sleep(TOKEN_DELAY)
                self._sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}},
                          event="content_block_delta")
            self._sse({"type": "content_block_stop", "index": 0}, event="content_block_stop")
        stop = {"end": "end_turn", "length": "max_tokens", "tool": "tool_use"}[g["stop"]]
        self._sse({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                   "usage": {"output_tokens": g["output"]}}, event="message_delta")
        self._sse({"type": "message_stop"}, event="message_stop")
        self._sse_end()


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def serve(port: int = 0, faults: set[str] | None = None) -> QuietServer:
    handler = type("H", (Handler,), {"state": State(faults or set())})
    server = QuietServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--faults", default=os.environ.get("MOCK_FAULTS", ""))
    a = ap.parse_args()
    s = serve(a.port, {f for f in a.faults.split(",") if f})
    print(f"mock on http://127.0.0.1:{s.server_port}  faults={a.faults or 'none'}")
    threading.Event().wait()
