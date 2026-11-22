"""Agent, tool-use and MCP scenarios (AG-xx). Each runs a real SDK against the mock."""
from __future__ import annotations

import json
from typing import Any

import anthropic
import openai

from casebook import checks
from casebook.mockserver import MockServer
from casebook.repro import make_client, request_kwargs

TOOL = {"name": "lookup_policy", "description": "Fetch a policy by number.",
        "input_schema": {"type": "object", "properties": {"policy": {"type": "string"}}, "required": ["policy"]}}
FN_TOOL = {"type": "function", "function": {"name": "issue_refund", "description": "Refund a claim.",
           "parameters": {"type": "object", "properties": {"claim_id": {"type": "string"}, "amount_usd": {"type": "number"}},
                          "required": ["claim_id", "amount_usd"], "additionalProperties": False}}}


def _a(client: anthropic.Anthropic, **kw: Any) -> dict[str, Any]:
    try:
        r = client.messages.create(**kw)
        return {"status": 200, "stop_reason": r.stop_reason, "content": [b.model_dump() for b in r.content],
                "container": (r.container.id if getattr(r, "container", None) else None)}
    except anthropic.APIStatusError as e:
        return {"status": e.status_code, "message": e.api_message}


def _turn(user_content: Any) -> list[dict[str, Any]]:
    return [{"role": "user", "content": "Look up policy P-77."},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01A", "name": "lookup_policy",
                                               "input": {"policy": "P-77"}}]},
            {"role": "user", "content": user_content}]


RESULT = {"type": "tool_result", "tool_use_id": "toolu_01A", "content": "Active, HO-3"}


def ag01(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0)
    kw = request_kwargs("anthropic", {"tools": [TOOL]})
    out = {"no_result": _a(c, **{**kw, "messages": _turn("What did you find?")}),
           "text_first": _a(c, **{**kw, "messages": _turn([{"type": "text", "text": "here:"}, RESULT])}),
           "fixed": _a(c, **{**kw, "messages": _turn([RESULT])})}
    out["finding"] = {"cls": "tools.result_pairing", "detail": f"missing result {out['no_result']['status']}, text before "
                      f"result {out['text_first']['status']}, result first {out['fixed']['status']}"}
    return out


def ag02(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0)
    web = {"type": "web_fetch_20250910", "name": "web_fetch"}
    mixed = [{"role": "user", "content": "Fetch the policy PDF and look up P-77."},
             {"role": "assistant", "content": [
                 {"type": "server_tool_use", "id": "srvtoolu_01B", "name": "web_fetch", "input": {"url": "https://example.com/p77.pdf"}},
                 {"type": "tool_use", "id": "toolu_01A", "name": "lookup_policy", "input": {"policy": "P-77"}}]}]
    out = {
        "text_added": _a(c, **request_kwargs("anthropic", {"tools": [TOOL, web], "messages": mixed + [
            {"role": "user", "content": [RESULT, {"type": "text", "text": "continue"}]}]})),
        "server_tool_dropped": _a(c, **request_kwargs("anthropic", {"tools": [TOOL], "messages": mixed + [
            {"role": "user", "content": [RESULT]}]})),
        "fixed": _a(c, **request_kwargs("anthropic", {"tools": [TOOL, web], "messages": mixed + [
            {"role": "user", "content": [RESULT]}]})),
    }
    out["finding"] = {"cls": "tools.mixed_server_client_turn", "detail": f"text added {out['text_added']['status']}, "
                      f"web_fetch dropped from tools {out['server_tool_dropped']['status']}, only tool_result + same tools "
                      f"{out['fixed']['status']}"}
    return out


def ag03(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0)
    tools = [{"type": "code_execution_20250825", "name": "code_execution"}]
    msgs = [{"role": "user", "content": "Summarise the claims CSV."}]
    first = _a(c, **request_kwargs("anthropic", {"tools": tools, "messages": msgs}))
    cont = msgs + [{"role": "assistant", "content": first["content"]}]
    forgot = _a(c, **request_kwargs("anthropic", {"tools": tools, "messages": cont}))
    kept = _a(c, **request_kwargs("anthropic", {"tools": tools, "messages": cont, "extra_body": {"container": first["container"]}}))
    out = {"first": {"stop_reason": first["stop_reason"], "container": first["container"]},
           "without_container": forgot["content"][0]["text"], "with_container": kept["content"][0]["text"]}
    out["finding"] = {"cls": "tools.pause_turn_container", "detail": "pause_turn continued without the container id: "
                      f"'{out['without_container'][:45]}…' (no error); with it: '{out['with_container'][:40]}'"}
    return out


def ag04(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0, headers={"X-Casebook-Org-ZDR": "true"})
    new = {"type": "web_search_20260209", "name": "web_search"}
    out = {"zdr_no_allowed_callers": _a(c, **request_kwargs("anthropic", {"tools": [new]})),
           "zdr_direct": _a(c, **request_kwargs("anthropic", {"tools": [{**new, "allowed_callers": ["direct"]}]})),
           "old_version": _a(c, **request_kwargs("anthropic", {"tools": [{"type": "web_search_20250305", "name": "web_search"}]})),
           "both_domain_lists": _a(c, **request_kwargs("anthropic", {"tools": [{**new, "allowed_callers": ["direct"],
                                   "allowed_domains": ["example.com"], "blocked_domains": ["bad.example"]}]}))}
    out["finding"] = {"cls": "tools.server_tool_config", "detail": f"ZDR org, web_search_20260209 without allowed_callers "
                      f"{out['zdr_no_allowed_callers']['status']}; with ['direct'] {out['zdr_direct']['status']}; both domain "
                      f"lists {out['both_domain_lists']['status']}"}
    return out


def ag05(srv: MockServer, cap: int = 3, runaway_limit: int = 20) -> dict[str, Any]:
    """A tool that always fails + a loop with no retry cap = the agent never ends."""
    c = make_client("anthropic", srv.url, max_retries=0, headers={"X-Casebook-Case": "tool_loop"})

    def loop(limit: int) -> int:
        msgs = [{"role": "user", "content": "Look up policy P-77."}]
        for n in range(1, limit + 1):
            r = c.messages.create(**request_kwargs("anthropic", {"tools": [TOOL], "messages": msgs}))
            if r.stop_reason != "tool_use":
                return n
            tu = next(b for b in r.content if b.type == "tool_use")
            msgs += [{"role": "assistant", "content": [b.model_dump() for b in r.content]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                                   "content": "policy service timeout"}]}]
        return limit

    uncapped = loop(runaway_limit)
    capped = loop(cap)
    return {"uncapped_calls": uncapped, "capped_calls": capped,
            "finding": {"cls": "tools.retry_loop", "detail": f"failing tool + no cap: still looping after {uncapped} model "
                        f"calls; capped at {cap}, then escalate with the last tool error"}}


def ag06(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    loose = {"type": "function", "name": "get_claim", "strict": True,
             "parameters": {"type": "object", "properties": {"claim_id": {"type": "string"}, "note": {"type": "string"}},
                            "required": ["claim_id"]}}
    fixed = {**loose, "parameters": {"type": "object", "properties": {"claim_id": {"type": "string"},
             "note": {"type": ["string", "null"]}}, "required": ["claim_id", "note"], "additionalProperties": False}}
    a = c.responses.create(model="gpt-6-luna", input="Get claim 4411.", tools=[loose])
    b = c.responses.create(model="gpt-6-luna", input="Get claim 4411.", tools=[fixed])
    out = {"loose_echo_strict": a.tools[0].strict, "fixed_echo_strict": b.tools[0].strict,
           "lint": checks.strict_schema_problems(loose["parameters"])}
    out["finding"] = {"cls": "schema.strict_downgraded", "detail": f"sent strict=true; echoed strict={out['loose_echo_strict']} "
                      f"for the loose schema, {out['fixed_echo_strict']} once fixed — no error either way"}
    return out


def ag07(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    kw = request_kwargs("openai", {"model": "gpt-4.1-nano-2025-04-14", "tools": [FN_TOOL]})
    dup = c.chat.completions.create(**kw).choices[0].message.tool_calls
    single = c.chat.completions.create(**kw, parallel_tool_calls=False).choices[0].message.tool_calls
    same = len({(t.function.name, t.function.arguments) for t in dup}) == 1
    out = {"parallel_calls": len(dup), "identical": same, "with_parallel_false": len(single)}
    out["finding"] = {"cls": "tools.duplicate_parallel_calls", "detail": f"{len(dup)} identical calls in one turn; "
                      f"parallel_tool_calls=false -> {len(single)}; dedupe on (name, arguments) before executing"}
    return out


def ag08(srv: MockServer | None) -> dict[str, Any]:
    prior = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAA…"},
             {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_claim", "arguments": "{}"}]
    bad_next = [prior[1], {"type": "function_call_output", "call_id": "call_1", "output": "open"}]
    good_next = prior + [{"type": "function_call_output", "call_id": "call_1", "output": "open"}]
    f = checks.stateless_replay(prior, bad_next)
    return {"bad": f.as_dict() if f else None, "good": checks.stateless_replay(prior, good_next),
            "finding": f.as_dict() if f else None}


def ag09(srv: MockServer, turns: int = 5) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    prev, billed = None, []
    for n in range(turns):
        r = c.responses.create(model="gpt-6-luna", input=f"Turn {n}: next step on claim 4411.",
                               **({"previous_response_id": prev} if prev else {}))
        billed.append(r.usage.input_tokens)
        prev = r.id
    return {"input_tokens_per_turn": billed, "total_input_billed": sum(billed),
            "finding": {"cls": "state.chain_billing", "detail": f"input tokens per turn {billed}: every turn re-bills the "
                        f"whole chain (total {sum(billed)}); use compaction / context_management for long chains"}}


def ag10(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    tool = {"type": "mcp", "server_label": "claims", "server_url": "https://mcp.example.com/auth/mcp",
            "require_approval": "never", "authorization": "token-placeholder"}
    t1 = c.responses.create(model="gpt-6-luna", input="Status of claim 4411?", tools=[tool])
    no_auth = {k: v for k, v in tool.items() if k != "authorization"}
    t2 = c.responses.create(model="gpt-6-luna", input="And 4412?", tools=[no_auth], previous_response_id=t1.id)
    t2b = c.responses.create(model="gpt-6-luna", input="And 4412?", tools=[tool], previous_response_id=t1.id)

    def summary(r: Any) -> list[str]:
        return [f"{o.type}{(': ' + o.error) if getattr(o, 'error', None) else ''}" for o in r.output]

    out = {"turn1": summary(t1), "turn2_no_token": summary(t2), "turn2_token_resent": summary(t2b),
           "echoed_authorization": any(t.model_dump().get("authorization") for t in t1.tools)}
    out["finding"] = {"cls": "mcp.authorization_not_stored", "detail": "turn 1 ok; turn 2 without authorization -> "
                      f"{out['turn2_no_token'][0][:70]}; resent -> ok"}
    return out


def ag11(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    tool = {"type": "mcp", "server_label": "claims", "server_url": "https://mcp.example.com/mcp"}
    r1 = c.responses.create(model="gpt-6-luna", input="Status of claim 4411?", tools=[tool])
    req = next(o for o in r1.output if o.type == "mcp_approval_request")
    r2 = c.responses.create(model="gpt-6-luna", tools=[tool], previous_response_id=r1.id,
                            input=[{"type": "mcp_approval_response", "approval_request_id": req.id, "approve": True}])
    out = {"turn1": [o.type for o in r1.output], "turn2": [o.type for o in r2.output]}
    out["finding"] = {"cls": "mcp.approval_pending", "detail": f"default require_approval: turn 1 {out['turn1']} (no call "
                      f"made, looks 'stuck'); after mcp_approval_response: {out['turn2']}"}
    return out


def ag12(srv: MockServer) -> dict[str, Any]:
    c = make_client("openai", srv.url, max_retries=0)
    out: dict[str, Any] = {}
    try:
        c.responses.create(model="gpt-6-luna", input="Find my files.",
                           tools=[{"type": "mcp", "server_label": "drive", "connector_id": "connector_googledrive",
                                   "require_approval": "never"}])
        out["connector_id"] = 200
    except openai.BadRequestError as e:
        out["connector_id"] = e.status_code
        out["message"] = (e.body or {}).get("message") if isinstance(e.body, dict) else str(e)
    ok = c.responses.create(model="gpt-6-luna", input="Find my files.",
                            tools=[{"type": "mcp", "server_label": "drive", "server_url": "https://mcp.example.com/mcp",
                                    "require_approval": "never"}])
    out["server_url"] = 200 if ok.id else None
    out["finding"] = {"cls": "mcp.connector_id_deprecated", "detail": f"connector_id on a post-2026-09-01 model "
                      f"{out['connector_id']}; server_url {out['server_url']}"}
    return out


def ag13(srv: MockServer) -> dict[str, Any]:
    beta = {"anthropic-beta": "mcp-client-2026-09-15"}
    c = make_client("anthropic", srv.url, max_retries=0, headers=beta)
    server = {"type": "url", "url": "https://mcp.example.com/claims", "name": "claims"}

    def run(tools: list[dict[str, Any]], servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return _a(c, **request_kwargs("anthropic", {"extra_body": {"mcp_servers": servers or [server]}, "tools": tools}))

    out = {"undefined_server": run([{"type": "mcp_toolset", "mcp_server_name": "claimz"}]),
           "unknown_tool_in_configs": run([{"type": "mcp_toolset", "mcp_server_name": "claims",
                                            "configs": {"search_claim": {"enabled": True}}}]),
           "correct": run([{"type": "mcp_toolset", "mcp_server_name": "claims",
                            "configs": {"get_claim": {"enabled": True}}}])}
    called = [b["type"] for b in out["unknown_tool_in_configs"].get("content", [])]
    out["finding"] = {"cls": "mcp.connector_config", "detail": f"undefined server {out['undefined_server']['status']}; "
                      f"typo in configs -> {out['unknown_tool_in_configs']['status']} with blocks {called} (silent no-op); "
                      f"correct -> {[b['type'] for b in out['correct']['content']][:2]}"}
    return out


def ag14(srv: MockServer) -> dict[str, Any]:
    c = make_client("anthropic", srv.url, max_retries=0, headers={"anthropic-beta": "mcp-client-2026-09-15"})
    local = _a(c, **request_kwargs("anthropic", {"extra_body": {"mcp_servers": [
        {"type": "url", "url": "http://localhost:3333/mcp", "name": "local"}]},
        "tools": [{"type": "mcp_toolset", "mcp_server_name": "local"}]}))
    return {"local_server": local, "finding": {"cls": "mcp.connector_needs_https",
            "detail": f"local/stdio-style server URL -> {local['status']}: the API connector needs a public https URL "
                      "(expose the stdio server over Streamable HTTP)"}}


def ag15(srv: MockServer | None) -> dict[str, Any]:
    bad = {"url": "http://claims.corp:8443/mcp", "own_oauth_client": True, "redirect_uris": ["https://localhost/callback"]}
    good = {"url": "https://mcp.example.com/mcp", "own_oauth_client": True,
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}
    problems = checks.connector_config_problems(bad)
    return {"bad": problems, "good": checks.connector_config_problems(good),
            "finding": {"cls": "mcp.claude_ai_connector_config", "detail": "; ".join(problems)}}


SCENARIOS = {"AG-01": ag01, "AG-02": ag02, "AG-03": ag03, "AG-04": ag04, "AG-05": ag05, "AG-06": ag06, "AG-07": ag07,
             "AG-08": ag08, "AG-09": ag09, "AG-10": ag10, "AG-11": ag11, "AG-12": ag12, "AG-13": ag13, "AG-14": ag14,
             "AG-15": ag15}
