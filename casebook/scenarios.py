"""Runners for packs that are not a single failing request.

  anomaly  HTTP 200, wrong outcome (tier downgrade, wrong org billed, prompt cache not working)
  client   the SDK refuses before sending anything
"""
from __future__ import annotations

from typing import Any

import anthropic

from casebook import checks, packs
from casebook import packet as pk
from casebook.mockserver import ORG_OPENAI, MockServer
from casebook.repro import make_client, request_kwargs

LONG_POLICY = "Claims handling policy. " + ("Clause: verify policy number, loss date, coverage and deductible. " * 120)
TOOLS = [
    {"name": "lookup_policy", "description": "Fetch a policy by number.",
     "input_schema": {"type": "object", "properties": {"policy": {"type": "string"}}, "required": ["policy"]}},
    {"name": "open_claim", "description": "Open a claim.",
     "input_schema": {"type": "object", "properties": {"policy": {"type": "string"}}, "required": ["policy"]}},
]


def run(pack_id: str, srv: MockServer) -> dict[str, Any]:
    from casebook import agent_scenarios, agent_sdk_scenarios
    from casebook.client_lab import scenarios as client_lab
    from casebook.identity import scenarios as identity
    from casebook.sims import s1_outage, s2_retry_storm, s3_silent_degradation
    from casebook.mcp_lab import scenarios as mcp_lab

    fn = {"OA-13": _oa13, "OA-15": _oa15, "AN-06": _an06, "AN-07": _an07, "AN-10": _an10, **_STREAM_AND_MIGRATION,
          **agent_scenarios.SCENARIOS, **agent_sdk_scenarios.SCENARIOS,
          **mcp_lab.SCENARIOS, **client_lab.SCENARIOS, **identity.SCENARIOS,
          "SIM-1": lambda _s: s1_outage.run(), "SIM-2": lambda _s: s2_retry_storm.run(), "SIM-3": lambda _s: s3_silent_degradation.run()}[pack_id]
    if srv is not None:
        srv.state.reset()
    out = fn(srv)
    out["pack"] = pack_id
    out["requests_sent"] = len(srv.state.requests) if srv is not None else 0
    return out


def _oa13(srv: MockServer) -> dict[str, Any]:
    client = make_client("openai", srv.url, pack_id="OA-13", max_retries=0)
    kw = request_kwargs("openai", packs.get("OA-13").meta["request"])
    resp = client.chat.completions.create(**kw)
    f = checks.service_tier(kw["service_tier"], resp.service_tier)
    return {"finding": f.as_dict() if f else None, "served": resp.service_tier, "request_id": resp._request_id}


def _oa15(srv: MockServer) -> dict[str, Any]:
    client = make_client("openai", srv.url, pack_id="OA-15", max_retries=0)
    raw = client.chat.completions.with_raw_response.create(**request_kwargs("openai"))
    p = pk.from_http("openai", raw.status_code, raw.headers, None, request_headers=raw.http_request.headers)
    f = checks.org_mismatch(ORG_OPENAI, p["org_id"])
    return {"finding": f.as_dict() if f else None, "packet": p}


def _anthropic_call(client: anthropic.Anthropic, system: Any, tools: list[dict[str, Any]] | None = None) -> dict[str, int]:
    kw = request_kwargs("anthropic", {"system": system})
    if tools:
        kw["tools"] = tools
    return client.messages.create(**kw).usage.model_dump()


def _an06(srv: MockServer) -> dict[str, Any]:
    """Short system prompt marked for caching: below the minimum, so both cache fields stay 0."""
    client = make_client("anthropic", srv.url, max_retries=0)
    system = [{"type": "text", "text": "You are a claims assistant.", "cache_control": {"type": "ephemeral"}}]
    usages = [_anthropic_call(client, system) for _ in range(2)]
    f = checks.cache(usages[1], cache_control_sent=True, repeat=True, pack="AN-06")
    return {"finding": f.as_dict() if f else None, "usages": usages}


def _an07(srv: MockServer) -> dict[str, Any]:
    """Same tools, different order on the second call: the cached prefix changes, so the cache is written again."""
    client = make_client("anthropic", srv.url, max_retries=0)
    system = [{"type": "text", "text": LONG_POLICY, "cache_control": {"type": "ephemeral"}}]
    first = _anthropic_call(client, system, TOOLS)
    drifted = _anthropic_call(client, system, list(reversed(TOOLS)))
    fixed = _anthropic_call(client, system, list(reversed(TOOLS)))  # stable order from now on -> read
    f = checks.cache(drifted, cache_control_sent=True, repeat=True, pack="AN-07")
    where = checks.first_prefix_change({"tools": TOOLS, "system": system}, {"tools": list(reversed(TOOLS)), "system": system})
    return {"finding": f.as_dict() if f else None, "where": where, "usages": [first, drifted, fixed]}


def _an10(srv: MockServer) -> dict[str, Any]:
    """Non-streaming call whose max_tokens implies >10 min: the SDK raises before sending."""
    client = make_client("anthropic", srv.url)
    kw = request_kwargs("anthropic", packs.get("AN-10").meta["request"])
    try:
        client.messages.create(**kw)
    except ValueError as e:
        return {"exception": "ValueError", "message": str(e), "request_id": None}
    return {"exception": None}



# ---------------------------------------------------------------- streams, structured output, batches

import openai  # noqa: E402
import pydantic  # noqa: E402


class ClaimSummary(pydantic.BaseModel):
    claim_id: str
    cause: str


def _oa16(srv: MockServer | None) -> dict[str, Any]:
    """Both SDKs default to a 10-minute read timeout: long calls die at exactly 600 s."""
    o = openai.OpenAI(api_key="sk-casebook-test")
    a = anthropic.Anthropic(api_key="sk-ant-casebook-test")
    return {"openai_timeout_s": o.timeout.read, "anthropic_timeout_s": a.timeout.read,
            "finding": {"cls": "client.timeout_default", "detail": f"SDK default read timeout: openai {o.timeout.read:.0f}s, "
                        f"anthropic {a.timeout.read:.0f}s — a failure at exactly that time is the client, not the API"}}


def _oa17(srv: MockServer) -> dict[str, Any]:
    """Stream with include_usage: last chunk has empty choices. Naive indexing crashes; interruption loses usage."""
    client = make_client("openai", srv.url, pack_id="OA-17", max_retries=0)
    kw = request_kwargs("openai", {"stream": True, "stream_options": {"include_usage": True}})
    naive_error = None
    try:
        with client.chat.completions.create(**kw) as stream:
            for chunk in stream:
                _ = chunk.choices[0].delta.content  # the common bug
    except IndexError as e:
        naive_error = f"IndexError: {e}"
    usage = None
    text = []
    with client.chat.completions.create(**kw) as stream:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text.append(chunk.choices[0].delta.content)
            if chunk.usage is not None:
                usage = chunk.usage.model_dump()
    cut_client = make_client("openai", srv.url, pack_id="OA-17", max_retries=0,
                             headers={"X-Casebook-Case": "cut"})
    cut_usage = None
    with cut_client.chat.completions.create(**kw) as stream:
        for chunk in stream:
            if chunk.usage is not None:
                cut_usage = chunk.usage.model_dump()
    f = checks.stream_usage(cut_usage, include_usage_requested=True)
    return {"naive_error": naive_error, "text": "".join(text), "usage": usage, "cut_usage": cut_usage,
            "finding": f.as_dict() if f else None}


def _oa18(srv: MockServer) -> dict[str, Any]:
    """Responses stream: HTTP 200, partial text, then response.failed. No exception is raised."""
    client = make_client("openai", srv.url, pack_id="OA-18", max_retries=0)
    text, last = [], None
    with client.responses.create(model="gpt-6-luna", input="Summarise claim #4411.", stream=True) as stream:
        for event in stream:
            last = event
            if event.type == "response.output_text.delta":
                text.append(event.delta)
    failed = last is not None and last.type == "response.failed"
    return {"partial_text": "".join(text), "last_event": last.type if last else None,
            "error": last.response.error.model_dump() if failed and last.response.error else None,
            "finding": {"cls": "stream.failed_event", "detail": "HTTP 200 stream ended with response.failed; "
                        "handle the event, not the status"} if failed else None}


def _oa19(srv: MockServer) -> dict[str, Any]:
    """Background stream drops mid-way; resume with starting_after=<last sequence_number>."""
    client = make_client("openai", srv.url, pack_id="OA-19", max_retries=0)
    seen: list[int] = []
    text: list[str] = []
    rid, last_type = None, None
    with client.responses.create(model="gpt-6-luna", input="Summarise claim #4411.", background=True, stream=True) as s:
        for e in s:
            rid = rid or e.response.id if e.type == "response.created" else rid
            seen.append(e.sequence_number)
            last_type = e.type
            if e.type == "response.output_text.delta":
                text.append(e.delta)
    dropped = last_type != "response.completed"
    resumed_from = seen[-1]
    with client.responses.retrieve(rid, stream=True, starting_after=resumed_from) as s:
        for e in s:
            seen.append(e.sequence_number)
            last_type = e.type
            if e.type == "response.output_text.delta":
                text.append(e.delta)
    return {"dropped": dropped, "resumed_from": resumed_from, "sequence": seen, "last_event": last_type,
            "text": "".join(text),
            "finding": {"cls": "stream.resumed", "detail": f"stream dropped after seq {resumed_from}; resumed with "
                        f"starting_after={resumed_from}; no gaps or duplicates"} if dropped else None}


def _oa21(srv: MockServer) -> dict[str, Any]:
    """Structured output: refusal -> parsed is None; length -> LengthFinishReasonError before parsing."""
    out: dict[str, Any] = {}
    for case in ("refusal", "length"):
        client = make_client("openai", srv.url, pack_id="OA-21", max_retries=0, headers={"X-Casebook-Case": case})
        try:
            resp = client.chat.completions.parse(**request_kwargs("openai"), response_format=ClaimSummary)
            msg = resp.choices[0].message
            out[case] = {"parsed": msg.parsed, "refusal": msg.refusal, "exception": None}
        except openai.LengthFinishReasonError as e:
            out[case] = {"parsed": None, "refusal": None, "exception": type(e).__name__}
    out["finding"] = {"cls": "output.refusal_or_length", "detail": f"refusal -> parsed={out['refusal']['parsed']} "
                      f"(message.refusal set); length -> {out['length']['exception']} before parsing"}
    return out


def _oa22(srv: MockServer | None) -> dict[str, Any]:
    loose = {"type": "object", "properties": {"claim_id": {"type": "string"}, "note": {"type": "string"}},
             "required": ["claim_id"]}
    fixed = {"type": "object", "properties": {"claim_id": {"type": "string"}, "note": {"type": ["string", "null"]}},
             "required": ["claim_id", "note"], "additionalProperties": False}
    problems = checks.strict_schema_problems(loose)
    return {"loose": problems, "fixed": checks.strict_schema_problems(fixed),
            "finding": {"cls": "schema.not_strict", "detail": "; ".join(problems)}}


def _an17(srv: MockServer) -> dict[str, Any]:
    """Same model, two outcomes: input over the window -> 400; input fits but input+max_tokens doesn't -> 200 cut."""
    client = make_client("anthropic", srv.url, max_retries=0)
    big = "Loss narrative. " * 300      # ~1,200 tokens by the mock's estimate
    medium = "Loss narrative. " * 140   # ~560 tokens
    out: dict[str, Any] = {}
    try:
        client.messages.create(model="mock-small-window", max_tokens=100, messages=[{"role": "user", "content": big}])
    except anthropic.BadRequestError as e:
        out["too_long"] = {"status": e.status_code, "message": e.api_message}
    r = client.messages.create(model="mock-small-window", max_tokens=800, messages=[{"role": "user", "content": medium}])
    f = checks.stop_reason("anthropic", r.stop_reason)
    out["window_full"] = {"status": 200, "stop_reason": r.stop_reason, "finding": f.as_dict() if f else None}
    out["finding"] = {"cls": "context.400_vs_200", "detail": f"input over window -> {out['too_long']['status']} "
                      f"'{out['too_long']['message'][:40]}…'; input fits but input+max_tokens doesn't -> 200 "
                      f"stop_reason={r.stop_reason}"}
    return out


def _an22(srv: MockServer) -> dict[str, Any]:
    client = make_client("anthropic", srv.url, max_retries=0)
    turn = [
        {"role": "user", "content": "Look up policy P-77."},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "lookup_policy",
                                           "input": {"policy": "P-77"}}]},
    ]
    bad = turn + [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Active, HO-3, $1,000 deductible"},
        {"type": "text", "text": "Now summarise it."}]}]
    good = turn + [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Active, HO-3, $1,000 deductible"}]}]
    rb = client.messages.create(**request_kwargs("anthropic", {"messages": bad, "tools": TOOLS}))
    rg = client.messages.create(**request_kwargs("anthropic", {"messages": good, "tools": TOOLS}))
    f = checks.tool_result_order(bad)
    return {"bad": {"output_tokens": rb.usage.output_tokens, "text": rb.content[0].text},
            "good": {"output_tokens": rg.usage.output_tokens, "text": rg.content[0].text},
            "finding": f.as_dict() if f else None, "lint_good": checks.tool_result_order(good)}


def _an23(srv: MockServer) -> dict[str, Any]:
    client = make_client("anthropic", srv.url, pack_id="AN-23", max_retries=0)
    raw = client.messages.with_raw_response.create(**request_kwargs("anthropic"))
    r = raw.parse()
    f = checks.stop_reason("anthropic", r.stop_reason)
    return {"status": raw.status_code, "stop_reason": r.stop_reason, "request_id": r._request_id,
            "finding": f.as_dict() if f else None}


def _an24(srv: MockServer) -> dict[str, Any]:
    client = make_client("anthropic", srv.url, max_retries=0)
    ids = [f"claim-{n:03d}" for n in range(10)]
    batch = client.messages.batches.create(requests=[
        {"custom_id": cid, "params": request_kwargs("anthropic")} for cid in ids])
    got = client.messages.batches.retrieve(batch.id)
    results = [r.model_dump() for r in client.messages.batches.results(batch.id)]
    rec = checks.batch_reconcile(ids, results)
    return {"request_counts": got.request_counts.model_dump(), "reconcile": rec,
            "finding": {"cls": "batch.reconcile", "detail": f"{rec['positional_mismatches']}/10 results out of "
                        f"request order; resubmit {len(rec['resubmit'])} (errored/expired are not billed)"}}


_STREAM_AND_MIGRATION = {"OA-16": _oa16, "OA-17": _oa17, "OA-18": _oa18, "OA-19": _oa19, "OA-21": _oa21, "OA-22": _oa22,
         "AN-17": _an17, "AN-22": _an22, "AN-23": _an23, "AN-24": _an24}


# ---------------------------------------------------------------- migrations

from casebook.mockserver import thinking_signature  # noqa: E402

FIRST_USER = "Summarise claim #4411 in one line."


def _try_anthropic(client: anthropic.Anthropic, **kw: Any) -> dict[str, Any]:
    try:
        r = client.messages.create(**kw)
        return {"status": 200, "stop_reason": r.stop_reason}
    except anthropic.APIStatusError as e:
        return {"status": e.status_code, "type": e.type, "message": e.api_message}
    except TypeError as e:  # the SDK itself no longer accepts the parameter: nothing was sent
        return {"status": None, "type": "TypeError (client-side, not sent)", "message": str(e)}


def _an18(srv: MockServer) -> dict[str, Any]:
    """Four documented migration 400s on newest models, each next to the fixed request that passes."""
    c = make_client("anthropic", srv.url, max_retries=0)
    base = request_kwargs("anthropic", {"model": "claude-opus-5-5"})
    prefill = [{"role": "user", "content": FIRST_USER}, {"role": "assistant", "content": "{"}]
    cases = {
        "prefill": (_try_anthropic(c, **{**base, "messages": prefill}),
                    _try_anthropic(c, **base)),
        "thinking_enabled": (_try_anthropic(c, **{**base, "thinking": {"type": "enabled", "budget_tokens": 2048}}),
                             _try_anthropic(c, **{**base, "thinking": {"type": "adaptive"}})),
        "sampling_params": (_try_anthropic(c, **{**base, "extra_body": {"temperature": 0.2}}),
                            _try_anthropic(c, **base)),
        "computer_tool_version": (
            _try_anthropic(c, **{**base, "tools": [{"type": "computer_20251124", "name": "computer",
                                                     "display_width_px": 1280, "display_height_px": 800}]}),
            _try_anthropic(c, **{**base, "tools": [{"type": "computer_toolset_20260801", "name": "computer"}]})),
    }
    out = {k: {"broken": a, "fixed": b} for k, (a, b) in cases.items()}
    out["finding"] = {"cls": "migration.400s", "detail": "; ".join(f"{k}: {v['broken']['status']}→{v['fixed']['status']}"
                                                                 for k, v in out.items())}
    return out


def _an19(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0)
    tools = [{**TOOLS[0], "strict": True}]
    out = {
        "sonnet_4_5_forced": _try_anthropic(c, **request_kwargs("anthropic", {
            "model": "claude-sonnet-4-5-20250929", "tools": TOOLS, "tool_choice": {"type": "tool", "name": "lookup_policy"}}),
            extra_headers={"X-Casebook-Date": "2026-11-19"}),
        "sonnet_5_5_forced": _try_anthropic(c, **request_kwargs("anthropic", {
            "model": "claude-sonnet-5-5", "tools": TOOLS, "tool_choice": {"type": "any"}})),
        "sonnet_5_5_auto_strict": _try_anthropic(c, **request_kwargs("anthropic", {
            "model": "claude-sonnet-5-5", "tools": tools, "tool_choice": {"type": "auto"}})),
    }
    out["finding"] = {"cls": "migration.forced_tool_choice",
                      "detail": f"same request: Sonnet 4.5 {out['sonnet_4_5_forced']['status']}, Sonnet 5.5 "
                                f"{out['sonnet_5_5_forced']['status']}; fix auto+strict -> {out['sonnet_5_5_auto_strict']['status']}"}
    return out


def _an20(srv: MockServer) -> dict[str, Any]:
    """Thinking blocks are bound to the conversation: editing earlier history invalidates them."""
    c = make_client("anthropic", srv.url, max_retries=0)
    sig = thinking_signature(FIRST_USER)
    history = [
        {"role": "user", "content": FIRST_USER},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "Check the policy first.", "signature": sig},
                                          {"type": "text", "text": "Water damage to the kitchen ceiling."}]},
        {"role": "user", "content": "Is it covered?"},
    ]
    edited = [{"role": "user", "content": FIRST_USER + " (redacted PII)"}] + history[1:]
    out = {"verbatim": _try_anthropic(c, **request_kwargs("anthropic", {"messages": history})),
           "edited_history": _try_anthropic(c, **request_kwargs("anthropic", {"messages": edited}))}
    out["finding"] = {"cls": "thinking.signature_bound",
                      "detail": f"verbatim history {out['verbatim']['status']}; first message edited "
                                f"{out['edited_history']['status']} — pass thinking blocks and earlier turns unmodified"}
    return out


def _an21(srv: MockServer) -> dict[str, Any]:
    """anthropic 2.x sends anthropic-version 2026-10-21; sampling params that worked before now 400."""
    sdk_default = make_client("anthropic", srv.url, max_retries=0)
    pinned = make_client("anthropic", srv.url, max_retries=0, headers={"anthropic-version": "2023-06-01"})
    via_extra = request_kwargs("anthropic", {"extra_body": {"temperature": 0.2}})
    out = {"sdk_version": anthropic.__version__,
           # 1) old code calling the new SDK: the SDK signature no longer has temperature
           "typed_param": _try_anthropic(sdk_default, **request_kwargs("anthropic", {"temperature": 0.2})),
           # 2) param forced through (extra_body, raw HTTP, other SDKs): the API version rejects it
           "sdk_default_version": _try_anthropic(sdk_default, **via_extra),
           "pinned_2023_06_01": _try_anthropic(pinned, **via_extra),
           "default_without_temperature": _try_anthropic(sdk_default, **request_kwargs("anthropic"))}
    sent = [r["headers"].get("anthropic-version") for r in srv.state.requests]
    out["versions_sent"] = sent
    out["finding"] = {"cls": "migration.api_version",
                      "detail": f"SDK {anthropic.__version__}: temperature= raises TypeError before sending; forced via "
                                f"extra_body under {sent[0]} -> {out['sdk_default_version']['status']}; pinned 2023-06-01 -> "
                                f"{out['pinned_2023_06_01']['status']}; dropping the param -> {out['default_without_temperature']['status']}"}
    return out


def _an26(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0)
    kw = request_kwargs("anthropic", {"model": "claude-sonnet-4-5-20250929"})
    out = {"2026-11-29": _try_anthropic(c, **kw, extra_headers={"X-Casebook-Date": "2026-11-29"}),
           "2026-11-30": _try_anthropic(c, **kw, extra_headers={"X-Casebook-Date": "2026-11-30"}),
           "replacement": _try_anthropic(c, **request_kwargs("anthropic", {"model": "claude-sonnet-5-5"}),
                                         extra_headers={"X-Casebook-Date": "2026-11-30"})}
    out["finding"] = {"cls": "model.retired", "detail": f"claude-sonnet-4-5-20250929: 11-29 {out['2026-11-29']['status']}, "
                      f"11-30 {out['2026-11-30']['status']} (status synthesized; capture live on 2026-12-01)"}
    return out


def _oa24(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)

    def call(model: str, day: str) -> dict[str, Any]:
        try:
            c.chat.completions.create(**request_kwargs("openai", {"model": model}), extra_headers={"X-Casebook-Date": day})
            return {"status": 200}
        except openai.APIStatusError as e:
            return {"status": e.status_code, "code": (e.body or {}).get("code") if isinstance(e.body, dict) else None}

    out = {"gpt-4o 2027-01-13": call("gpt-4o", "2027-01-13"), "gpt-4o 2027-01-14": call("gpt-4o", "2027-01-14"),
           "gpt-4o-mini 2027-01-14": call("gpt-4o-mini", "2027-01-14")}
    out["finding"] = {"cls": "model.shutdown", "detail": "gpt-4o: 2027-01-13 200, 2027-01-14 "
                      f"{out['gpt-4o 2027-01-14']['status']} (status synthesized; docs don't publish the shutdown error)"}
    return out


_STREAM_AND_MIGRATION.update({"AN-18": _an18, "AN-19": _an19, "AN-20": _an20, "AN-21": _an21, "AN-26": _an26, "OA-24": _oa24})
