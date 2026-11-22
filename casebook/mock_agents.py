"""Agent / tool-use / MCP behaviour for the mock.

Every rule cites the doc it reproduces. Where the docs give the exact 400 text it is used verbatim;
where they only describe the failure, the text is synthesized and the pack's provenance says so.

Anthropic  https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools
           https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
           https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
OpenAI     https://developers.openai.com/api/docs/guides/tools-connectors-mcp
           https://developers.openai.com/api/docs/guides/function-calling
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Any

Err = tuple[int, dict[str, Any]]


def _aerr(rid: str, msg: str, status: int = 400) -> Err:
    return status, {"type": "error", "error": {"type": "invalid_request_error", "message": msg}, "request_id": rid}


def _blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
    c = msg.get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


# ---------------------------------------------------------------- Anthropic request validation

def anthropic_rules(payload: dict[str, Any], headers: dict[str, str], rid: str) -> Err | None:
    msgs = payload.get("messages") or []
    tools = payload.get("tools") or []
    tool_types = {t.get("type", "custom") for t in tools}

    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        blocks = _blocks(m)
        client_ids = [b["id"] for b in blocks if b.get("type") == "tool_use"]
        result_ids = {b.get("tool_use_id") for b in blocks if b.get("type", "").endswith("_tool_result")}
        server_open = [b for b in blocks if b.get("type") == "server_tool_use" and b.get("id") not in result_ids]
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        nxt_blocks = _blocks(nxt) if nxt else []
        # AG-02: unresolved server tool — continuation must contain only tool_result blocks and the same tools
        for st in server_open:
            name = st.get("name", "web_fetch")
            if not any(t.startswith(name) for t in tool_types):
                return _aerr(rid, f"`{name}` tool use with id `{st['id']}` was found, but no `{name}` tool was provided")
            if nxt is not None and (isinstance(nxt.get("content"), str) or any(b.get("type") == "text" for b in nxt_blocks)):
                return _aerr(rid, f"`{name}` tool use with id `{st['id']}` was found without a corresponding "
                                  f"`{name}_tool_result` block")
        if not client_ids or nxt is None:
            continue
        # AG-01: every tool_use needs a tool_result in the very next message, results first
        got = [b.get("tool_use_id") for b in nxt_blocks if b.get("type") == "tool_result"]
        missing = [t for t in client_ids if t not in got]
        if nxt.get("role") != "user" or missing:
            ids = ", ".join(missing or client_ids)
            return _aerr(rid, f"`tool_use` ids were found without `tool_result` blocks immediately after: {ids}. "
                              "Each `tool_use` block must have a corresponding `tool_result` block in the next message.")
        kinds = [b.get("type") for b in nxt_blocks]
        if "text" in kinds and kinds.index("text") < max(j for j, k in enumerate(kinds) if k == "tool_result"):
            return _aerr(rid, "tool_result blocks must come first in the content array; text must come after all tool results")

    # AG-04: newer web tools under ZDR need allowed_callers ["direct"]; allowed/blocked domains are exclusive
    zdr = headers.get("x-casebook-org-zdr") == "true"
    for t in tools:
        ttype = t.get("type", "")
        if ttype.startswith(("web_search_", "web_fetch_")):
            if t.get("allowed_domains") and t.get("blocked_domains"):
                return _aerr(rid, f"tools.{ttype}: allowed_domains and blocked_domains cannot both be set")
            version = ttype.rsplit("_", 1)[-1]
            if zdr and version >= "20260209" and t.get("allowed_callers") != ["direct"]:
                return _aerr(rid, f"tools.{ttype}: this organization requires zero data retention; set "
                                  'allowed_callers: ["direct"] to use this tool version')

    # AG-13 / AG-14: MCP connector validation
    servers = payload.get("mcp_servers") or []
    if servers or any(t.get("type") == "mcp_toolset" for t in tools):
        beta = headers.get("anthropic-beta", "")
        if "mcp-client-2026-09-15" not in beta and "mcp-client-2025-11-20" not in beta:
            return _aerr(rid, "mcp_servers: requires the anthropic-beta header mcp-client-2026-09-15")
        names = [s.get("name") for s in servers]
        for s in servers:
            if not str(s.get("url", "")).startswith("https://"):
                return _aerr(rid, f"mcp_servers.{s.get('name')}.url: must start with https://")
        toolsets = [t for t in tools if t.get("type") == "mcp_toolset"]
        for ts in toolsets:
            if ts.get("mcp_server_name") not in names:
                return _aerr(rid, f"mcp_toolset references undefined MCP server '{ts.get('mcp_server_name')}'")
        for n in names:
            refs = sum(1 for ts in toolsets if ts.get("mcp_server_name") == n)
            if refs != 1:
                return _aerr(rid, f"MCP server '{n}' must be referenced by exactly one mcp_toolset (found {refs})")
    return None


def anthropic_reply(payload: dict[str, Any], state: Any, base_body: dict[str, Any],
                    case: str | None = None) -> dict[str, Any] | None:
    """Successful-but-tricky replies: pause_turn containers (AG-03), error-retry loops (AG-05),
    MCP connector silent no-op (AG-13)."""
    tools = payload.get("tools") or []
    msgs = payload.get("messages") or []
    # AG-03: code-execution server loop pauses; the continuation must carry the same container id
    if any(str(t.get("type", "")).startswith("code_execution_") for t in tools):
        last = msgs[-1] if msgs else {}
        if last.get("role") != "assistant":  # first call of the turn
            cid = "container_" + secrets.token_hex(6)
            with state.lock:
                state.containers[cid] = {"report.csv"}
            return {**base_body, "stop_reason": "pause_turn", "container": {"id": cid, "expires_at": "2026-11-19T23:59:00Z"},
                    "content": [{"type": "server_tool_use", "id": "srvtoolu_" + secrets.token_hex(6), "name": "code_execution",
                                 "input": {"code": "df.to_csv('report.csv')"}},
                                {"type": "text", "text": "Wrote report.csv; continuing analysis."}]}
        cid = payload.get("container")
        with state.lock:
            files = state.containers.get(cid, set()) if cid else set()
        if not cid:
            cid = "container_" + secrets.token_hex(6)  # a fresh, empty container
        text = ("report.csv found: 4,411 claims summarised." if "report.csv" in files
                else "FileNotFoundError: report.csv — nothing from earlier in the turn is here.")
        return {**base_body, "stop_reason": "end_turn", "container": {"id": cid, "expires_at": "2026-11-19T23:59:00Z"},
                "content": [{"type": "text", "text": text}]}
    # AG-05: a client tool keeps failing and the model keeps calling it
    if tools and msgs and case == "tool_loop":
        lastb = _blocks(msgs[-1])
        first_call = msgs[-1].get("role") == "user" and isinstance(msgs[-1].get("content"), str)
        if first_call or any(b.get("type") == "tool_result" and b.get("is_error") for b in lastb):
            name = next((t.get("name") for t in tools if t.get("name")), "lookup_policy")
            return {**base_body, "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "toolu_" + secrets.token_hex(6), "name": name,
                                 "input": {"policy": "P-77"}}]}
    # AG-13: valid MCP config, but configs names a tool the server doesn't have -> no call, no error
    toolsets = [t for t in tools if t.get("type") == "mcp_toolset"]
    if toolsets:
        configs = toolsets[0].get("configs") or {}
        known = {"search_claims", "get_claim"}
        enabled = [n for n, c in configs.items() if c.get("enabled", True)] if configs else list(known)
        callable_ = [n for n in enabled if n in known]
        if not callable_:
            return {**base_body, "content": [{"type": "text", "text": "I don't have a tool for that."}]}
        return {**base_body, "content": [
            {"type": "mcp_tool_use", "id": "mcptoolu_" + secrets.token_hex(6), "name": callable_[0],
             "server_name": toolsets[0]["mcp_server_name"], "input": {"claim_id": "4411"}},
            {"type": "mcp_tool_result", "tool_use_id": "mcptoolu_x", "is_error": False,
             "content": [{"type": "text", "text": "claim 4411: open"}]},
            {"type": "text", "text": "Claim 4411 is open."}]}
    return None


def agent_tool_events(payload: dict[str, Any], tool: dict[str, Any]) -> list[tuple[str, dict[str, Any]]] | None:
    """Streamed tool_use for the Claude Agent SDK packs (AG-22..24): ask for `tool` once, then finish."""
    if not payload.get("tools"):
        return None
    msgs = payload.get("messages") or []
    answered = any(b.get("type") == "tool_result" for m in msgs for b in _blocks(m))
    model = payload.get("model", "mock")
    start = ("message_start", {"type": "message_start", "message": {
        "id": "msg_" + secrets.token_hex(6), "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 1}}})
    if answered:
        return [start,
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                   "usage": {"output_tokens": 1}}),
                ("message_stop", {"type": "message_stop"})]
    tid = "toolu_" + secrets.token_hex(8)
    return [start,
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "tool_use", "id": tid, "name": tool["name"], "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool["input"])}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                               "usage": {"output_tokens": 20}}),
            ("message_stop", {"type": "message_stop"})]


# ---------------------------------------------------------------- OpenAI Chat Completions tool calls

def chat_tool_calls(payload: dict[str, Any], mode: str | None) -> dict[str, Any] | None:
    """AG-07 duplicate parallel calls; AG-26 a model that never stops calling tools; AG-28 refund flow."""
    tools = payload.get("tools") or []
    if not tools:
        return None
    name = tools[0]["function"]["name"]
    msgs = payload.get("messages") or []
    has_result = any(m.get("role") == "tool" for m in msgs)

    def call(args: dict[str, Any]) -> dict[str, Any]:
        return {"id": "call_" + secrets.token_hex(6), "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    if payload.get("model") == "gpt-4.1-nano-2025-04-14" and not has_result:
        args = {"claim_id": "4411"}
        n = 1 if payload.get("parallel_tool_calls") is False else 2
        return {"role": "assistant", "content": None, "tool_calls": [call(args) for _ in range(n)]}
    if mode == "always_tool_call":
        return {"role": "assistant", "content": None, "tool_calls": [call({"claim_id": "4411"})]}
    if mode == "refund_flow" and not has_result:
        return {"role": "assistant", "content": None, "tool_calls": [call({"claim_id": "4411", "amount_usd": 1000})]}
    return None


# ---------------------------------------------------------------- OpenAI Responses (non-stream)

NEW_MODELS = ("gpt-6",)  # released after 2026-09-01 in this mock's world


def responses_reply(payload: dict[str, Any], state: Any, rid: str) -> Err:
    model = payload.get("model", "gpt-6-luna")
    tools = payload.get("tools") or []
    inp = payload.get("input")
    items = inp if isinstance(inp, list) else ([{"role": "user", "content": inp}] if inp else [])

    def oerr(msg: str, param: str | None = None) -> Err:
        return 400, {"error": {"message": msg, "type": "invalid_request_error", "param": param, "code": None}}

    output: list[dict[str, Any]] = []
    echo_tools = []
    for t in tools:
        if t.get("type") == "mcp":
            if t.get("connector_id") and model.startswith(NEW_MODELS):
                return oerr("connector_id is not supported for this model; use server_url or tunnel_id", "tools")
            if not (t.get("server_url") or t.get("connector_id") or t.get("tunnel_id")):
                return oerr("mcp tool requires server_url, connector_id or tunnel_id", "tools")
        echo = dict(t)
        if t.get("type") == "function" and t.get("strict"):
            from casebook.checks import strict_schema_problems  # local import: avoid cycle at module load
            if strict_schema_problems(t.get("parameters") or {}):
                echo["strict"] = False  # AG-06: not strict-compatible -> silently not strict
        echo.pop("authorization", None)  # never echoed or stored
        echo_tools.append(echo)

    # usage chain (AG-09): previous_response_id bills every earlier input token in the chain
    this_in = max(1, len(json.dumps(items)) // 4)
    prev = payload.get("previous_response_id")
    with state.lock:
        prior = state.chains.get(prev, 0) if prev else 0
    input_tokens = prior + this_in

    approvals = {i.get("approval_request_id"): i.get("approve") for i in items
                 if isinstance(i, dict) and i.get("type") == "mcp_approval_response"}
    for t in [t for t in tools if t.get("type") == "mcp"]:
        label = t.get("server_label", "claims")
        needs_auth = "auth" in str(t.get("server_url", ""))
        if needs_auth and not t.get("authorization"):
            # AG-10: the token is not stored, so turn 2 without it fails at the MCP server (shape synthesized)
            output.append({"type": "mcp_list_tools", "id": "mcpl_" + secrets.token_hex(4), "server_label": label,
                           "tools": [], "error": "Error retrieving tool list from MCP server: 401 Unauthorized"})
            continue
        output.append({"type": "mcp_list_tools", "id": "mcpl_" + secrets.token_hex(4), "server_label": label,
                       "tools": [{"name": "get_claim", "input_schema": {"type": "object"}}]})
        if t.get("require_approval", "always") != "never" and not approvals:
            output.append({"type": "mcp_approval_request", "id": "mcpr_" + secrets.token_hex(6), "server_label": label,
                           "name": "get_claim", "arguments": json.dumps({"claim_id": "4411"})})
        elif approvals and not all(approvals.values()):
            output.append({"type": "message", "id": "msg_x", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": "The tool call was not approved.", "annotations": []}]})
        else:
            output.append({"type": "mcp_call", "id": "mcp_" + secrets.token_hex(6), "server_label": label,
                           "name": "get_claim", "arguments": json.dumps({"claim_id": "4411"}),
                           "output": "claim 4411: open", "error": None})
    if not output or output[-1]["type"] == "mcp_call":
        output.append({"type": "message", "id": "msg_" + secrets.token_hex(4), "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": "ok", "annotations": []}]})
    resp_id = "resp_" + secrets.token_hex(8)
    with state.lock:
        state.chains[resp_id] = input_tokens + 5
    return 200, {"id": resp_id, "object": "response", "created_at": int(time.time()), "status": "completed",
                 "model": model, "output": output, "tools": echo_tools, "parallel_tool_calls": True, "tool_choice": "auto",
                 "previous_response_id": prev,
                 "usage": {"input_tokens": input_tokens, "output_tokens": 5, "total_tokens": input_tokens + 5,
                           "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
