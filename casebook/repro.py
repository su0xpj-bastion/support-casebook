"""Run a fault pack through the real SDKs against the mock.

Error packs (kind: error) run two ways:
  sdk-default  official client, default settings (max_retries=2) — what most customers run
  policy       max_retries=0 + casebook.policy (decides per attempt from the Support Packet)
The number that matters is how many requests each way sends for one user action.

Anomaly packs (kind: anomaly) return HTTP 200 with the wrong outcome; client packs (kind: client)
fail inside the SDK before any request is sent. Both are run by casebook.scenarios.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import anthropic
import openai

from casebook import packet as pk
from casebook import packs, policy, rules
from casebook.mockserver import MockServer

OPENAI_MODEL = "gpt-6-luna"
ANTHROPIC_MODEL = "claude-sonnet-5-5"
PROMPT = "Summarise claim #4411 in one line."


def make_client(provider: str, base_url: str, *, pack_id: str | None = None, max_retries: int = 2,
                timeout: float | None = None, headers: dict[str, str] | None = None) -> Any:
    hdrs = dict(headers or {})
    if pack_id:
        hdrs["X-Casebook-Pack"] = pack_id
    kw: dict[str, Any] = {"max_retries": max_retries, "default_headers": hdrs}
    if timeout is not None:
        kw["timeout"] = timeout
    if provider == "openai":
        return openai.OpenAI(api_key="sk-casebook-test", base_url=f"{base_url}/v1", **kw)
    return anthropic.Anthropic(api_key="sk-ant-casebook-test", base_url=base_url, **kw)


def request_kwargs(provider: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    if provider == "openai":
        kw: dict[str, Any] = {"model": OPENAI_MODEL, "messages": [{"role": "user", "content": PROMPT}]}
    else:
        kw = {"model": ANTHROPIC_MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": PROMPT}]}
    kw.update(overrides or {})
    return kw


def _sdk(provider: str) -> dict[str, str]:
    mod = openai if provider == "openai" else anthropic
    return {"name": f"{provider}-python", "version": mod.__version__}


def run(pack_id: str, srv: MockServer, mode: str, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    pack = packs.get(pack_id)
    meta = pack.meta
    provider = pack.provider
    req = meta.get("request", {})
    client_cfg = meta.get("client", {})
    kwargs = request_kwargs(provider, req)
    srv.state.reset()
    exc: Exception | None = None
    trace: list[dict[str, Any]] = []
    partial = ""
    started = time.monotonic()

    retries = 2 if mode == "sdk-default" else 0
    client = make_client(provider, srv.url, pack_id=pack_id, max_retries=retries,
                         timeout=client_cfg.get("timeout"), headers=meta.get("request_headers"))
    try:
        if kwargs.get("stream"):
            if mode == "policy":
                return {"pack": pack_id, "mode": mode, "requests_sent": None, "skipped": "stream packs: policy n/a"}
            partial = _consume_stream(provider, client, kwargs)
        elif mode == "sdk-default":
            create = client.chat.completions.create if provider == "openai" else client.messages.create
            create(**kwargs)
        elif mode == "policy":
            call = policy.call_openai if provider == "openai" else policy.call_anthropic
            call(client, trace=trace, sleep=sleep, **kwargs)
        else:
            raise ValueError(f"unknown mode {mode}")
    except (openai.APIStatusError, anthropic.APIStatusError, openai.APIConnectionError,
            anthropic.APIConnectionError) as e:
        exc = e
        partial = getattr(e, "partial_text", "")
    elapsed = time.monotonic() - started

    packet = None
    if isinstance(exc, (openai.APITimeoutError, anthropic.APITimeoutError)):
        packet = pk.from_client_error(provider, exc, "client_timeout", model=kwargs["model"], sdk=_sdk(provider),
                                      provenance=pack.provenance)
    elif isinstance(exc, (openai.APIConnectionError, anthropic.APIConnectionError)):
        packet = pk.from_client_error(provider, exc, "connection_error", model=kwargs["model"], sdk=_sdk(provider),
                                      provenance=pack.provenance)
    elif exc is not None:
        packet = pk.from_sdk_error(provider, exc, model=kwargs["model"], sdk=_sdk(provider), provenance=pack.provenance)

    return {
        "pack": pack_id,
        "mode": mode,
        "requests_sent": srv.state.hits.get(pack_id, 0),
        "request_ids_seen": [r["request_id"] for r in srv.state.requests if r["key"] == pack_id],
        "elapsed_s": round(elapsed, 2),
        "exception": f"{type(exc).__module__.split('.')[0]}.{type(exc).__name__}" if exc else None,
        "partial_text": partial,
        "packet": packet,
        "verdict": rules.classify(packet).as_dict() if packet else None,
        "trace": trace,
    }


def _consume_stream(provider: str, client: Any, kwargs: dict[str, Any]) -> str:
    """Iterate a stream, keeping text received before any mid-stream error."""
    parts: list[str] = []
    try:
        if provider == "anthropic":
            with client.messages.create(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        parts.append(event.delta.text)
        else:
            with client.chat.completions.create(**kwargs) as stream:
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        parts.append(chunk.choices[0].delta.content)
    except (openai.APIStatusError, anthropic.APIStatusError) as e:
        e.partial_text = "".join(parts)  # type: ignore[attr-defined]
        raise
    return "".join(parts)
