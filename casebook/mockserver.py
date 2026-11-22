"""Mock OpenAI + Anthropic endpoint that replays fault packs to the real SDKs.

  POST /v1/chat/completions   OpenAI Chat Completions
  POST /v1/messages           Anthropic Messages (JSON or SSE when "stream": true)
  GET  /_casebook/hits        per-pack request counts (how many times the client actually called)
  GET  /_casebook/requests    request log: pack, path, headers, body, response request id
  POST /_casebook/reset       zero the counters, the log and the prompt cache

Select a fault with the request header `X-Casebook-Pack: AN-01` (set via the SDK's
default_headers). Without it the mock answers like the real API, including Anthropic
prompt-cache accounting (used by AN-06 / AN-07).

expected.json "mode" values:
  replay    (default) status + headers + JSON body
  raw       non-JSON body (e.g. a CDN 413 HTML page); "omit_base_headers" drops request-id etc.
  delay     sleep "delay_s" then answer with the success body (client-side timeout packs)
  sse_error 200 + SSE stream that emits partial text, then `event: error` with "body"
  success   normal success, with "headers" and a shallow "body_merge" applied (200-anomaly packs)

The SDK's own retry, exception and header handling is what gets exercised — nothing is faked
on the client side.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import string
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from casebook import mock_agents, models, packs

_B62 = string.ascii_letters + string.digits
ORG_OPENAI = "org-casebook0001"
ORG_ANTHROPIC = "11111111-2222-3333-4444-555555555555"
WORKSPACE = "wrkspc_casebook0001"
CACHE_MIN_TOKENS = 1024  # Anthropic per-model minimum is 512–4,096; one value is enough for the mock
# Small context windows so AN-17 can be reproduced without megabyte prompts.
MODEL_WINDOWS = {"mock-small-window": 1000}


def _openai_rid() -> str:
    return "req_" + secrets.token_hex(16)


def _anthropic_rid() -> str:
    return "req_011" + "".join(secrets.choice(_B62) for _ in range(21))


def _next_month_first() -> str:
    now = datetime.now(timezone.utc)
    first = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first.strftime("%B %-d, %Y")


def _fill(value: Any, subs: dict[str, str]) -> Any:
    if isinstance(value, str):
        for k, v in subs.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, subs) for v in value]
    return value


def ntok(obj: Any) -> int:
    return max(1, len(json.dumps(obj)) // 4)


def _input_tokens(payload: dict[str, Any]) -> int:
    return ntok(payload.get("messages", [])) + (ntok(payload["system"]) if payload.get("system") else 0) \
        + (ntok(payload["tools"]) if payload.get("tools") else 0)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: dict[str, int] = {}
        self.requests: list[dict[str, Any]] = []
        self.cache: set[str] = set()
        self.responses: dict[str, list[dict[str, Any]]] = {}
        self.batches: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        self.containers: dict[str, set[str]] = {}
        self.chains: dict[str, int] = {}
        self.flags: dict[str, int] = {}

    def record(self, key: str, path: str, headers: dict[str, str], body: Any, rid: str) -> None:
        with self.lock:
            self.hits[key] = self.hits.get(key, 0) + 1
            self.requests.append({"key": key, "path": path, "headers": headers, "body": body,
                                  "request_id": rid, "t": time.time()})

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()
            self.requests.clear()
            self.cache.clear()
            self.responses.clear()
            self.batches.clear()
            self.containers.clear()
            self.chains.clear()
            self.flags.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "casebook-mock/0.2"
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, *_: Any) -> None:  # quiet
        pass

    def _send(self, status: int, body: Any, headers: dict[str, str], *, raw: bytes | None = None,
              content_type: str = "application/json") -> None:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (timeout packs) — expected

    def do_GET(self) -> None:  # noqa: N802
        with self.state.lock:
            if self.path == "/_casebook/hits":
                self._send(200, dict(self.state.hits), {})
                return
            if self.path == "/_casebook/requests":
                self._send(200, list(self.state.requests), {})
                return
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path.startswith("/v1/models"):
            pack_id = self.headers.get("x-casebook-pack")
            if pack_id:
                exp = packs.get(pack_id).expected
                self.state.record(pack_id + ":models", self.path, {k.lower(): v for k, v in self.headers.items()}, None, "")
                if exp.get("mode") == "outage" and self._in_outage(exp):
                    self._send(503, {"error": {"message": "Service Unavailable", "type": "service_unavailable_error",
                                               "param": None, "code": None}}, self._base_headers("openai"))
                    return
                if exp.get("mode") == "outage":
                    self._send(200, {"object": "list", "data": [{"id": "gpt-6-luna", "object": "model", "created": 0,
                                                                 "owned_by": "casebook"}]}, self._base_headers("openai"))
                    return
            self._models(path)
            return
        if path.startswith("/v1/messages/batches/"):
            self._batch_get(path)
            return
        if path.startswith("/v1/responses/"):
            self._responses_resume(path.rsplit("/", 1)[1], params)
            return
        self._send(404, {"error": "not found"}, {})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/_casebook/reset":
            self.state.reset()
            self._send(200, {"ok": True}, {})
            return
        route = self.path.partition("?")[0]  # Claude Code calls /v1/messages?beta=true
        if route.endswith("/messages/count_tokens"):
            self._count_tokens(payload)
            return
        if route.endswith("/messages/batches"):
            self._batch_create(payload)
            return
        if route.endswith("/responses"):
            self._responses_create(payload)
            return
        if route.endswith("/chat/completions"):
            provider = "openai"
        elif route.endswith("/messages"):
            provider = "anthropic"
        else:
            self.state.record("unknown-route", self.path, {k.lower(): v for k, v in self.headers.items()}, payload, "")
            self._send(404, {"error": "unknown route"}, {})
            return

        pack_id = self.headers.get("x-casebook-pack")
        base = self._base_headers(provider)
        rid = base.get("x-request-id") or base.get("request-id", "")
        self.state.record(pack_id or f"{provider}:ok", self.path,
                          {k.lower(): v for k, v in self.headers.items()}, payload, rid)

        if not pack_id:
            self._ok(provider, payload, rid, base)
            return
        try:
            pack = packs.get(pack_id)
        except KeyError:
            self._send(400, {"error": {"message": f"unknown casebook pack {pack_id}"}}, base)
            return
        if pack.provider != provider:
            self._send(400, {"error": {"message": f"pack {pack_id} is {pack.provider}, route is {provider}"}}, base)
            return

        exp = pack.expected
        subs = {"request_id": rid, "regain_date": _next_month_first()}
        mode = exp.get("mode", "replay")
        headers = {} if exp.get("omit_base_headers") else dict(base)
        headers.update(_fill(exp.get("headers", {}), subs))
        if mode == "replay":
            self._send(exp["status"], _fill(exp["body"], subs), headers)
        elif mode == "raw":
            self._send(exp["status"], None, headers, raw=exp["raw_body"].encode(),
                       content_type=exp.get("content_type", "text/html"))
        elif mode == "delay":
            time.sleep(float(exp["delay_s"]))
            try:
                self._ok(provider, payload, rid, headers)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif mode == "success":
            case = self.headers.get("x-casebook-case")
            cfg = exp["cases"][case] if case else exp
            self._ok(provider, payload, rid, headers, body_merge=_fill(cfg.get("body_merge", {}), subs),
                     stream_opts=cfg.get("stream_opts"))
        elif mode == "agent_tool":
            events = mock_agents.agent_tool_events(payload, exp["tool"]) if payload.get("stream") else None
            if events:
                self._sse(events, headers)
            else:
                self._ok(provider, payload, rid, headers)
        elif mode in ("always_tool_call", "refund_flow"):
            if mode == "refund_flow" and any(m.get("role") == "tool" for m in payload.get("messages", [])):
                with self.state.lock:
                    n = self.state.flags.get("refund_followups", 0)
                    self.state.flags["refund_followups"] = n + 1
                if n == 0:  # the follow-up after the side effect times out once
                    time.sleep(float(exp.get("delay_s", 2)))
            try:
                self._ok(provider, payload, rid, headers, tool_mode=mode)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif mode == "outage":
            if self._in_outage(exp) and not self._key_revoked():
                self.close_connection = True   # drop the connection without a response: client sees a connection error
                return
            if self._key_revoked():
                self._send(401, {"error": {"message": "Incorrect API key provided: sk-proj-****dead.", "type": "invalid_request_error",
                                           "param": None, "code": "invalid_api_key"}}, headers)
                return
            self._ok(provider, payload, rid, headers)
        elif mode == "degraded_slice":
            from casebook.sims.s3_silent_degradation import CORRUPT, degraded

            sess = self.headers.get("x-session-id", "")
            platform = self.headers.get("x-platform", "first_party")
            bad = degraded(sess, float(exp["rates"].get(platform, 0)))
            body_merge = {"content": [{"type": "text", "text": CORRUPT if bad else "Claim 4411: water damage to the kitchen ceiling."}]}
            self._ok(provider, payload, rid, headers, body_merge=body_merge)
        elif mode == "capacity":
            ok = self._capacity_take(exp)
            if ok:
                self._ok(provider, payload, rid, headers)
            else:
                self._send(503, {"error": {"message": "The server is overloaded. Please try again later.",
                                           "type": "service_unavailable_error", "param": None,
                                           "code": "server_is_overloaded"}}, headers)
        elif mode == "sse_error":
            self._sse_error(payload, rid, headers, _fill(exp["body"], subs), exp.get("partial_text", "Partial"))
        else:
            self._send(500, {"error": {"message": f"unknown mode {mode}"}}, headers)

    # ---------------------------------------------------------------- success paths

    def _ok(self, provider: str, payload: dict[str, Any], rid: str, headers: dict[str, str],
            body_merge: dict[str, Any] | None = None, stream_opts: dict[str, Any] | None = None,
            tool_mode: str | None = None) -> None:
        if provider == "anthropic" and payload.get("stream"):
            self._sse_ok(payload, rid, headers)
            return
        if provider == "openai" and payload.get("stream"):
            self._openai_stream(payload, headers, stream_opts or {})
            return
        if provider == "anthropic":
            bad = self._anthropic_validate(payload, rid) or mock_agents.anthropic_rules(
                payload, {k.lower(): v for k, v in self.headers.items()}, rid)
            if bad:
                self._send(bad[0], bad[1], headers)
                return
        if provider == "openai" and models.is_retired(payload.get("model", ""), self._today()):
            # Shutdown error code is not documented; historically 404 model_not_found (synthesized, OA-24).
            m = payload.get("model")
            self._send(404, {"error": {"message": f"The model `{m}` does not exist or you do not have access to it.",
                                       "type": "invalid_request_error", "param": None, "code": "model_not_found"}}, headers)
            return
        body = self._success(provider, payload)
        if provider == "anthropic":
            body = mock_agents.anthropic_reply(payload, self.state, body, self.headers.get("x-casebook-case")) or body
        if provider == "openai":
            tc = mock_agents.chat_tool_calls(payload, tool_mode)
            if tc:
                body["choices"][0]["message"] = tc
                body["choices"][0]["finish_reason"] = "tool_calls"
        if body_merge and "choices" in body_merge and isinstance(body_merge["choices"], dict):
            body["choices"][0].update(body_merge["choices"])  # merge into the first choice
            body_merge = {k: v for k, v in body_merge.items() if k != "choices"}
        body.update(body_merge or {})
        self._send(200, body, headers)

    # ---------------------------------------------------------------- outage model (Sim 1)

    def _sim_elapsed(self) -> float:
        with self.state.lock:
            t0 = self.state.flags.setdefault("sim_t0", time.monotonic())
        return time.monotonic() - t0

    def _in_outage(self, exp: dict[str, Any]) -> bool:
        t = self._sim_elapsed()
        return float(exp["outage_start_s"]) <= t < float(exp["outage_end_s"])

    def _key_revoked(self) -> bool:
        return "dead" in (self.headers.get("authorization") or "")

    # ---------------------------------------------------------------- capacity model (Sim 1 / Sim 2)

    def _capacity_take(self, exp: dict[str, Any]) -> bool:
        """Token bucket whose refill rate follows exp['schedule'] = [{"until_s", "rps"}, ...] measured from the
        first request after a reset. Rejected requests still cost exp['reject_cost'] tokens — the documented
        'failed requests count toward limits' — so retry storms slow recovery."""
        with self.state.lock:
            f = self.state.flags
            now = time.monotonic()
            t0 = f.setdefault("cap_t0", now)
            last = f.get("cap_last", now)
            rps = next(seg["rps"] for seg in exp["schedule"] if now - t0 < seg["until_s"])
            burst = max(1.0, rps * float(exp.get("burst_s", 0.25)))
            tokens = min(burst, f.get("cap_tokens", burst) + (now - last) * rps)
            f["cap_last"] = now
            if tokens >= 1:
                f["cap_tokens"] = tokens - 1
                return True
            f["cap_tokens"] = tokens - float(exp.get("reject_cost", 0.5))
            return False

    # ---------------------------------------------------------------- Anthropic request rules

    def _today(self) -> date:
        """Mock clock: X-Casebook-Date lets a test stand on the far side of a retirement date."""
        raw = self.headers.get("x-casebook-date")
        return date.fromisoformat(raw) if raw else datetime.now(timezone.utc).date()

    def _anthropic_validate(self, payload: dict[str, Any], rid: str) -> tuple[int, dict[str, Any]] | None:
        """Documented request-level failures, enforced by model and API version (AN-17..21, AN-26)."""
        def err(status: int, etype: str, msg: str) -> tuple[int, dict[str, Any]]:
            return status, {"type": "error", "error": {"type": etype, "message": msg}, "request_id": rid}

        model = payload.get("model", "")
        version = self.headers.get("anthropic-version", "")
        if models.is_retired(model, self._today()):
            # Status/message for retired models are NOT documented: synthesized 404 (provenance-tagged in AN-26).
            return err(404, "not_found_error", f"model: {model}")
        window = MODEL_WINDOWS.get(model)
        if window is not None and _input_tokens(payload) > window:
            return err(400, "invalid_request_error", f"prompt is too long: {_input_tokens(payload)} tokens > {window} maximum")
        tc = payload.get("tool_choice") or {}
        if model in models.NO_FORCED_TOOL_CHOICE and tc.get("type") in ("any", "tool"):
            return err(400, "invalid_request_error", 'tool_choice: type "tool" and "any" are not supported for this model.')
        msgs = payload.get("messages") or []
        paused = msgs and isinstance(msgs[-1].get("content"), list) and any(
            isinstance(b, dict) and b.get("type") == "server_tool_use" for b in msgs[-1]["content"])
        # A pause_turn continuation legitimately ends with the assistant's paused content (AG-03).
        if models.no_prefill(model) and msgs and msgs[-1].get("role") == "assistant" and not paused:
            return err(400, "invalid_request_error",
                       "This model does not support assistant message prefill. The conversation must end with a user message.")
        thinking = payload.get("thinking") or {}
        if models.no_thinking_enabled(model) and thinking.get("type") == "enabled":
            return err(400, "invalid_request_error",
                       '"thinking.type.enabled" is not supported for this model. Use "thinking.type.adaptive" and '
                       '"output_config.effort" to control thinking behavior.')
        if version >= models.API_VERSION_NO_SAMPLING:
            used = [k for k in models.SAMPLING_PARAMS if k in payload]
            if used:
                # Removal is documented; the exact message is not -> synthesized wording.
                return err(400, "invalid_request_error",
                           f"{used[0]}: Extra inputs are not permitted in API version {version}")
        for tool in payload.get("tools") or []:
            ttype = tool.get("type", "")
            if ttype.startswith("computer_") and ttype != models.COMPUTER_TOOL_CURRENT and model.startswith(("claude-opus-5", "claude-sonnet-5")):
                return err(400, "invalid_request_error",
                           f"'{model}' does not support tool types: {ttype}. Did you mean one of: {models.COMPUTER_TOOL_CURRENT}?")
        bad = _bad_thinking_signature(msgs)
        if bad:
            return err(400, "invalid_request_error",
                       f"messages.{bad[0]}.content.{bad[1]}: Invalid `signature` in `thinking` block. "
                       "The block is bound to a different conversation.")
        return None

    # ---------------------------------------------------------------- OpenAI streaming

    def _openai_stream(self, payload: dict[str, Any], headers: dict[str, str], opts: dict[str, Any]) -> None:
        """Chat Completions SSE. With stream_options.include_usage the final chunk has empty choices.

        opts.cut_before_usage drops the connection before that chunk (OA-17: usage lost on interruption).
        """
        base = {"id": "chatcmpl-casebook", "object": "chat.completion.chunk", "created": int(time.time()),
                "model": payload.get("model", "mock")}
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {"content": "Claim #4411: "}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {"content": "water damage."}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": opts.get("finish_reason", "stop")}]},
        ]
        include_usage = (payload.get("stream_options") or {}).get("include_usage")
        if include_usage and not opts.get("cut_before_usage"):
            pt = ntok(payload.get("messages", []))
            chunks.append({**base, "choices": [], "usage": {"prompt_tokens": pt, "completion_tokens": 6,
                                                              "total_tokens": pt + 6}})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
            if not opts.get("cut_before_usage"):
                self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    # ---------------------------------------------------------------- OpenAI Responses (OA-18, OA-19)

    def _responses_events(self, rid: str, failed: bool) -> list[dict[str, Any]]:
        """Responses API stream in the documented order: created, in_progress, output_item.added,
        content_part.added, output_text.delta..., output_text.done, content_part.done,
        output_item.done, completed (or response.failed partway, OA-18)."""
        resp = {"id": rid, "object": "response", "status": "in_progress", "output": [], "model": "gpt-6-luna"}
        words = ["Claim ", "#4411: ", "water ", "damage, ", "kitchen ", "ceiling."]
        item_id = "msg_" + rid[-8:]
        events: list[dict[str, Any]] = [
            {"type": "response.created", "response": resp},
            {"type": "response.in_progress", "response": resp},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "message", "id": item_id, "role": "assistant", "status": "in_progress", "content": []}},
            {"type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": 0,
             "part": {"type": "output_text", "text": "", "annotations": []}},
        ]
        for n, word in enumerate(words):
            events.append({"type": "response.output_text.delta", "item_id": item_id, "output_index": 0,
                           "content_index": 0, "delta": word})
            if failed and n == 1:
                events.append({"type": "response.failed", "response": {**resp, "status": "failed",
                               "error": {"code": "server_error", "message": "The model failed to generate a response."}}})
                break
        if not failed:
            text = "".join(words)
            part = {"type": "output_text", "text": text, "annotations": []}
            item = {"type": "message", "id": item_id, "role": "assistant", "status": "completed", "content": [part]}
            events += [
                {"type": "response.output_text.done", "item_id": item_id, "output_index": 0, "content_index": 0, "text": text},
                {"type": "response.content_part.done", "item_id": item_id, "output_index": 0, "content_index": 0, "part": part},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {**resp, "status": "completed", "output": [item],
                 "usage": {"input_tokens": 20, "output_tokens": 9, "total_tokens": 29,
                           "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}},
            ]
        for i, e in enumerate(events):
            e["sequence_number"] = i
        return events

    def _responses_create(self, payload: dict[str, Any]) -> None:
        pack_id = self.headers.get("x-casebook-pack") or "openai:responses"
        rid = "resp_casebook" + secrets.token_hex(6)
        base = self._base_headers("openai")
        self.state.record(pack_id, self.path, {k.lower(): v for k, v in self.headers.items()}, payload,
                          base["x-request-id"])
        events = self._responses_events(rid, failed=pack_id == "OA-18")
        with self.state.lock:
            self.state.responses[rid] = events
        if not payload.get("stream"):
            if payload.get("background"):
                self._send(200, {"id": rid, "object": "response", "status": "queued", "output": [],
                                 "model": payload.get("model")}, base)
                return
            status, body = mock_agents.responses_reply(payload, self.state, rid)
            self._send(status, body, base)
            return
        cut = int(packs.get("OA-19").expected.get("drop_after", 3)) if pack_id == "OA-19" else None
        self._sse_lines(events[: cut + 1] if cut is not None else events, base)

    def _responses_resume(self, rid: str, params: dict[str, str]) -> None:
        with self.state.lock:
            events = self.state.responses.get(rid)
        base = self._base_headers("openai")
        self.state.record("OA-19:resume", self.path, {k.lower(): v for k, v in self.headers.items()}, params,
                          base["x-request-id"])
        if events is None:
            self._send(404, {"error": {"message": f"No response found with id '{rid}'.", "type": "invalid_request_error",
                                       "param": None, "code": None}}, base)
            return
        after = int(params.get("starting_after", "-1"))
        if params.get("stream") == "true":
            self._sse_lines([e for e in events if e["sequence_number"] > after], base)
        else:
            self._send(200, events[-1]["response"], base)

    def _sse_lines(self, events: list[dict[str, Any]], headers: dict[str, str]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            for e in events:
                self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _count_tokens(self, payload: dict[str, Any]) -> None:
        base = self._base_headers("anthropic")
        self.state.record("count_tokens", self.path, {k.lower(): v for k, v in self.headers.items()}, payload, base["request-id"])
        tc = payload.get("tool_choice") or {}
        if payload.get("model") in models.NO_FORCED_TOOL_CHOICE and tc.get("type") in ("any", "tool"):
            self._send(400, {"type": "error", "error": {"type": "invalid_request_error",
                             "message": 'tool_choice: type "tool" and "any" are not supported for this model.'},
                             "request_id": base["request-id"]}, base)
            return
        self._send(200, {"input_tokens": _input_tokens(payload)}, base)

    # ---------------------------------------------------------------- models endpoint

    def _models(self, path: str) -> None:
        """GET /v1/models[/{id}]. Claude Code checks the model here before its first message."""
        base = self._base_headers("anthropic")
        self.state.record("models", self.path, {k.lower(): v for k, v in self.headers.items()}, None, base["request-id"])
        known = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-sonnet-4-5-20250929"]
        today = self._today()

        def info(mid: str) -> dict[str, Any]:
            return {"type": "model", "id": mid, "display_name": mid, "created_at": "2026-01-01T00:00:00Z"}

        parts = path.strip("/").split("/")
        if len(parts) == 2:
            data = [info(m) for m in known if not models.is_retired(m, today)]
            self._send(200, {"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]}, base)
            return
        mid = parts[2]
        if mid in known and not models.is_retired(mid, today) or mid.startswith("claude-haiku") or mid in MODEL_WINDOWS:
            self._send(200, info(mid), base)
            return
        self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": f"model: {mid}"},
                         "request_id": base["request-id"]}, base)

    # ---------------------------------------------------------------- Anthropic Message Batches (AN-24)

    def _batch_create(self, payload: dict[str, Any]) -> None:
        base = self._base_headers("anthropic")
        bid = "msgbatch_casebook" + secrets.token_hex(6)
        self.state.record(self.headers.get("x-casebook-pack") or "anthropic:batch", self.path,
                          {k.lower(): v for k, v in self.headers.items()}, {"n": len(payload.get("requests", []))},
                          base["request-id"])
        reqs = payload.get("requests", [])
        results = []
        for i, r in enumerate(reqs):
            kind = "succeeded" if i % 5 not in (3, 4) else ("errored" if i % 5 == 3 else "expired")
            line: dict[str, Any] = {"custom_id": r["custom_id"], "result": {"type": kind}}
            if kind == "succeeded":
                line["result"]["message"] = {**self._success("anthropic", r["params"]),
                                             "content": [{"type": "text", "text": f"answer for {r['custom_id']}"}]}
            elif kind == "errored":
                line["result"]["error"] = {"type": "error", "error": {"type": "invalid_request_error",
                                                                    "message": "max_tokens: Field required"}}
            results.append(line)
        results.reverse()  # docs: results may be in any order — match on custom_id
        counts = {k: sum(1 for x in results if x["result"]["type"] == k)
                  for k in ("succeeded", "errored", "expired", "canceled")}
        counts["processing"] = 0
        now = datetime.now(timezone.utc)
        batch = {"id": bid, "type": "message_batch", "processing_status": "ended", "request_counts": counts,
                 "created_at": now.isoformat(), "ended_at": now.isoformat(),
                 "expires_at": (now + timedelta(hours=24)).isoformat(), "archived_at": None,
                 "cancel_initiated_at": None, "results_url": None}
        with self.state.lock:
            self.state.batches[bid] = (batch, results)
        self._send(200, batch, base)

    def _batch_get(self, path: str) -> None:
        base = self._base_headers("anthropic")
        parts = path.strip("/").split("/")  # v1 messages batches <id> [results]
        with self.state.lock:
            entry = self.state.batches.get(parts[3])
        if entry is None:
            self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": "batch not found"},
                             "request_id": base["request-id"]}, base)
            return
        batch, results = entry
        if len(parts) == 5 and parts[4] == "results":
            raw = "\n".join(json.dumps(r) for r in results).encode() + b"\n"
            self._send(200, None, base, raw=raw, content_type="application/binary")
            return
        host, port = self.server.server_address[:2]
        self._send(200, {**batch, "results_url": f"http://{host}:{port}/v1/messages/batches/{batch['id']}/results"}, base)

    def _success(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        model = payload.get("model", "mock")
        if provider == "openai":
            out: dict[str, Any] = {
                "id": "chatcmpl-casebook",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": ntok(payload.get("messages", [])), "completion_tokens": 1,
                          "total_tokens": ntok(payload.get("messages", [])) + 1},
            }
            if "service_tier" in payload:
                out["service_tier"] = payload["service_tier"] if payload["service_tier"] != "auto" else "default"
            return out
        content: list[dict[str, Any]] = [{"type": "text", "text": "ok"}]
        stop = "end_turn"
        usage = self._anthropic_usage(payload)
        window = MODEL_WINDOWS.get(model)
        if window is not None and _input_tokens(payload) + int(payload.get("max_tokens", 0)) > window:
            # Claude 4.5+: accepted, generation stops when the window is full (AN-17, 200 branch)
            stop = "model_context_window_exceeded"
            usage["output_tokens"] = max(1, window - _input_tokens(payload))
        if _text_after_tool_result(payload):
            # Documented failure: text appended after tool_result blocks -> near-empty reply (AN-22)
            content = [{"type": "text", "text": "."}]
            usage["output_tokens"] = 2
        return {
            "id": "msg_casebook",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": usage,
        }

    def _anthropic_usage(self, payload: dict[str, Any]) -> dict[str, int]:
        """Prompt-cache accounting: prefix = tools -> system -> messages up to the cache_control mark.

        Documented behaviour reproduced: below the minimum nothing is cached and both cache
        fields are 0 (no error); any change earlier in the prefix (e.g. tool order) is a miss.
        """
        total = ntok(payload.get("messages", [])) + ntok(payload.get("system", "")) + ntok(payload.get("tools", []))
        prefix = _cached_prefix(payload)
        usage = {"input_tokens": total, "output_tokens": 1,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        if prefix is None:
            return usage
        ptoks = ntok(prefix)
        if ptoks < CACHE_MIN_TOKENS:
            return usage
        key = hashlib.sha256(json.dumps(prefix, sort_keys=False).encode()).hexdigest()
        with self.state.lock:
            hit = key in self.state.cache
            self.state.cache.add(key)
        usage["input_tokens"] = max(1, total - ptoks)
        usage["cache_read_input_tokens" if hit else "cache_creation_input_tokens"] = ptoks
        return usage

    def _sse(self, events: list[tuple[str, dict[str, Any]]], headers: dict[str, str]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        try:
            for name, data in events:
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _stream_head(self, payload: dict[str, Any], text: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_casebook", "type": "message", "role": "assistant", "model": payload.get("model", "mock"),
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": ntok(payload.get("messages", [])), "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("ping", {"type": "ping"}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
        ]

    def _sse_ok(self, payload: dict[str, Any], rid: str, headers: dict[str, str]) -> None:
        events = self._stream_head(payload, "ok") + [
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        self._sse(events, headers)

    def _sse_error(self, payload: dict[str, Any], rid: str, headers: dict[str, str],
                   err_body: dict[str, Any], partial: str) -> None:
        self._sse(self._stream_head(payload, partial) + [("error", err_body)], headers)

    def _base_headers(self, provider: str) -> dict[str, str]:
        if provider == "openai":
            return {
                "x-request-id": _openai_rid(),
                "openai-organization": ORG_OPENAI,
                "openai-processing-ms": "12",
                "openai-version": "2020-10-01",
            }
        return {
            "request-id": _anthropic_rid(),
            "anthropic-organization-id": ORG_ANTHROPIC,
            "anthropic-workspace-id": WORKSPACE,
        }


def thinking_signature(first_user_text: str) -> str:
    """Mock signature: bound to the conversation's first user message (stand-in for the real binding)."""
    return "sig-" + hashlib.sha256(first_user_text.encode()).hexdigest()[:16]


def _bad_thinking_signature(msgs: list[dict[str, Any]]) -> tuple[int, int] | None:
    first = next((m for m in msgs if m.get("role") == "user"), None)
    if first is None:
        return None
    text = first["content"] if isinstance(first["content"], str) else json.dumps(first["content"])
    want = thinking_signature(text)
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue
        for j, b in enumerate(m["content"]):
            if isinstance(b, dict) and b.get("type") == "thinking" and b.get("signature") != want:
                return i, j
    return None


def _text_after_tool_result(payload: dict[str, Any]) -> bool:
    msgs = payload.get("messages") or []
    if not msgs or msgs[-1].get("role") != "user" or not isinstance(msgs[-1].get("content"), list):
        return False
    kinds = [b.get("type") for b in msgs[-1]["content"] if isinstance(b, dict)]
    return "tool_result" in kinds and kinds[-1] == "text" and kinds.index("tool_result") < len(kinds) - 1


def _cached_prefix(payload: dict[str, Any]) -> list[Any] | None:
    """Everything up to and including the last block carrying cache_control, in tools->system->messages order."""
    order: list[Any] = []
    last = -1
    for tool in payload.get("tools", []) or []:
        order.append(("tool", tool))
        if isinstance(tool, dict) and "cache_control" in tool:
            last = len(order) - 1
    system = payload.get("system")
    for block in system if isinstance(system, list) else ([{"type": "text", "text": system}] if system else []):
        order.append(("system", block))
        if isinstance(block, dict) and "cache_control" in block:
            last = len(order) - 1
    for msg in payload.get("messages", []):
        content = msg.get("content")
        for block in content if isinstance(content, list) else [{"type": "text", "text": content}]:
            order.append((msg.get("role"), block))
            if isinstance(block, dict) and "cache_control" in block:
                last = len(order) - 1
    if last < 0:
        return None
    return [[kind, {k: v for k, v in blk.items() if k != "cache_control"} if isinstance(blk, dict) else blk]
            for kind, blk in order[: last + 1]]


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return  # clients that time out and hang up are part of the scenarios
        super().handle_error(request, client_address)


class MockServer:
    """Run the mock on 127.0.0.1:<free port> in a background thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, tls: tuple[str, str] | None = None) -> None:
        """tls=(cert_chain_pem, key_pem) serves HTTPS — used by the TLS-inspection client packs."""
        self.state = State()
        handler = type("BoundHandler", (Handler,), {"state": self.state})
        self.httpd = _QuietServer((host, port), handler)
        self.scheme = "http"
        if tls:
            import ssl

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
            self.scheme = "https"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"{self.scheme}://{host}:{port}"

    def __enter__(self) -> "MockServer":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="casebook mock server")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = MockServer(port=args.port)
    print(f"casebook mock on {srv.url}  (header X-Casebook-Pack selects a fault)", flush=True)
    try:
        srv.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
