"""The real agent SDKs against the mock.

Claude Agent SDK (bundled Claude Code CLI) for the permission pipeline (AG-22..24), and the
OpenAI Agents SDK over Chat Completions for run limits, guardrails, state options and side
effects (AG-26..28). Claude Code runs with an isolated, empty config dir and no setting sources,
so the user's own ~/.claude settings never leak into a scenario.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import warnings
from pathlib import Path
from typing import Any

import openai

from casebook.mockserver import MockServer
from casebook.repro import make_client, request_kwargs

PROOF = "casebook-proof.txt"


# ---------------------------------------------------------------- Claude Agent SDK

def _claude_env(srv: MockServer, cfg: str, pack: str) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": srv.url,
        "ANTHROPIC_API_KEY": "sk-ant-casebook-test",
        "ANTHROPIC_CUSTOM_HEADERS": f"X-Casebook-Pack: {pack}",
        "CLAUDE_CONFIG_DIR": cfg,
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


async def _run_claude(srv: MockServer, pack: str, opts_kw: dict[str, Any], use_callback: bool) -> dict[str, Any]:
    from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, query

    calls: list[str] = []

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
        calls.append(tool_name)
        return PermissionResultAllow()

    srv.state.reset()
    with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as work:
        opts = ClaudeAgentOptions(env=_claude_env(srv, cfg, pack), cwd=work, max_turns=3, setting_sources=[],
                                  can_use_tool=can_use_tool if use_callback else None, **opts_kw)

        async def prompt():  # can_use_tool requires streaming input
            yield {"type": "user", "message": {"role": "user", "content": "Create the proof file."}}

        result: dict[str, Any] = {}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                async for msg in query(prompt=prompt(), options=opts):
                    if type(msg).__name__ == "ResultMessage":
                        result = {"subtype": msg.subtype, "is_error": msg.is_error, "num_turns": msg.num_turns}
            except Exception as e:  # noqa: BLE001 — record whatever the SDK raised
                result = {"exception": type(e).__name__, "message": str(e)[:300]}
        ran = (Path(work) / PROOF).exists()
        tool_results = [b for r in srv.state.requests if r["key"] == pack and isinstance(r["body"], dict)
                        for m in r["body"].get("messages", []) if isinstance(m.get("content"), list)
                        for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        last = tool_results[-1] if tool_results else {}
        content = last.get("content")
        text = content if isinstance(content, str) else " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
        sdk_warnings = [str(w.message) for w in caught if "can_use_tool" in str(w.message)]
    return {"tool_ran": ran, "callback_calls": calls, "tool_result_is_error": bool(last.get("is_error")),
            "tool_result_text": text[:200], "result": result, "sdk_warnings": sdk_warnings}


def _claude(srv: MockServer, pack: str, opts_kw: dict[str, Any], use_callback: bool = False) -> dict[str, Any]:
    return asyncio.run(_run_claude(srv, pack, opts_kw, use_callback))


def ag22(srv: MockServer) -> dict[str, Any]:
    """Headless run, no allow rule / hook / callback: default mode denies the Bash call."""
    denied = _claude(srv, "AG-22", {})
    allowed = _claude(srv, "AG-22", {"allowed_tools": ["Bash"]})
    return {"default_mode": denied, "with_allow_rule": allowed,
            "finding": {"cls": "agent.headless_denied", "detail": f"no approval path: tool ran={denied['tool_ran']}, tool "
                        f"result error={denied['tool_result_is_error']} ('… needs approval'), yet the run ended "
                        f"subtype={denied['result'].get('subtype')} is_error={denied['result'].get('is_error')}; "
                        f"allowed_tools=['Bash']: ran={allowed['tool_ran']}"}}


def ag23(srv: MockServer) -> dict[str, Any]:
    """canUseTool never fires for a tool an allow rule already approves."""
    shadowed = _claude(srv, "AG-23", {"allowed_tools": ["Bash"]}, use_callback=True)
    reaches = _claude(srv, "AG-23", {}, use_callback=True)
    return {"allow_rule_plus_callback": shadowed, "callback_only": reaches,
            "finding": {"cls": "agent.can_use_tool_shadowed", "detail": f"allowed_tools=['Bash'] + can_use_tool: callback "
                        f"calls={shadowed['callback_calls']} (tool ran={shadowed['tool_ran']}); callback alone: "
                        f"calls={reaches['callback_calls']}; SDK warning: {bool(shadowed['sdk_warnings'])}"}}


def ag24(srv: MockServer) -> dict[str, Any]:
    """allowed_tools is not a restriction: under bypassPermissions an unlisted tool still runs."""
    unlisted = _claude(srv, "AG-24", {"allowed_tools": ["Read"], "permission_mode": "bypassPermissions"})
    removed = _claude(srv, "AG-24", {"allowed_tools": ["Read"], "disallowed_tools": ["Bash"],
                                     "permission_mode": "bypassPermissions"})
    return {"allowed_read_only_bypass": unlisted, "disallowed_bash": removed,
            "finding": {"cls": "agent.allowed_tools_not_a_restriction", "detail": f"allowed_tools=['Read'] + bypassPermissions: "
                        f"Bash ran={unlisted['tool_ran']}; disallowed_tools=['Bash']: ran={removed['tool_ran']}"}}


# ---------------------------------------------------------------- OpenAI Agents SDK

def _agents_model(srv: MockServer, pack: str | None, timeout: float | None = None) -> Any:
    from agents import OpenAIChatCompletionsModel

    client = openai.AsyncOpenAI(api_key="sk-casebook-test", base_url=f"{srv.url}/v1", max_retries=0,
                                default_headers={"X-Casebook-Pack": pack} if pack else None,
                                **({"timeout": timeout} if timeout else {}))
    return OpenAIChatCompletionsModel(model="gpt-6-luna", openai_client=client)


def ag26(srv: MockServer) -> dict[str, Any]:
    from agents import (Agent, GuardrailFunctionOutput, InputGuardrailTripwireTriggered, MaxTurnsExceeded, Runner,
                        function_tool, input_guardrail, set_tracing_disabled)

    set_tracing_disabled(True)

    @function_tool
    def issue_refund(claim_id: str, amount_usd: float) -> str:
        """Refund a claim."""
        return "queued"

    out: dict[str, Any] = {}
    srv.state.reset()
    agent = Agent(name="claims", instructions="Help with claims.", tools=[issue_refund], model=_agents_model(srv, "AG-26"))
    try:
        Runner.run_sync(agent, "Refund claim 4411.", max_turns=3)
        out["max_turns"] = None
    except MaxTurnsExceeded as e:
        out["max_turns"] = {"exception": type(e).__name__, "message": str(e)[:120]}
    out["model_calls"] = len(srv.state.requests)

    @input_guardrail
    async def no_card_numbers(ctx: Any, agent: Any, text: Any) -> GuardrailFunctionOutput:
        flagged = "4111 1111 1111 1111" in str(text)
        return GuardrailFunctionOutput(output_info={"pan": flagged}, tripwire_triggered=flagged)

    srv.state.reset()
    guarded = Agent(name="claims", instructions="Help.", input_guardrails=[no_card_numbers], model=_agents_model(srv, None))
    try:
        Runner.run_sync(guarded, "Refund to card 4111 1111 1111 1111 please.")
        out["guardrail"] = None
    except InputGuardrailTripwireTriggered as e:
        out["guardrail"] = {"exception": type(e).__name__}
    out["guardrail_model_calls"] = len(srv.state.requests)
    out["finding"] = {"cls": "agent.run_limits", "detail": f"model never stops calling tools: {out['max_turns']['exception'] if out['max_turns'] else 'no error'} "
                      f"after {out['model_calls']} model calls; input guardrail tripwire -> "
                      f"{(out['guardrail'] or {}).get('exception')} (model calls made: {out['guardrail_model_calls']})"}
    return out


def ag27(srv: MockServer) -> dict[str, Any]:
    from agents import Agent, Runner, SQLiteSession, set_tracing_disabled

    set_tracing_disabled(True)
    agent = Agent(name="claims", instructions="Help.", model=_agents_model(srv, None))
    try:
        Runner.run_sync(agent, "Status of claim 4411?", session=SQLiteSession("casebook"), previous_response_id="resp_123")
        out: dict[str, Any] = {"conflict": None}
    except Exception as e:  # noqa: BLE001
        out = {"conflict": {"exception": type(e).__name__, "message": str(e)[:200]}}
    out["finding"] = {"cls": "agent.state_conflict", "detail": f"session + previous_response_id -> "
                      f"{(out['conflict'] or {}).get('exception', 'accepted')}: {(out['conflict'] or {}).get('message', '')[:120]}"}
    return out


def ag28(srv: MockServer) -> dict[str, Any]:
    """The refund executed, then the follow-up model call timed out. Replaying the turn re-runs the
    model, which asks for the refund again with a NEW call_id — dedupe on call_id alone misses it."""
    ledger: list[dict[str, Any]] = []

    def issue_refund(claim_id: str, amount_usd: float, call_id: str, seen: dict[str, str] | None) -> str:
        key = f"refund:{claim_id}:{amount_usd}"
        if seen is not None and key in seen:
            return f"already refunded (call {seen[key]})"
        ledger.append({"claim_id": claim_id, "amount_usd": amount_usd, "call_id": call_id})
        if seen is not None:
            seen[key] = call_id
        return "refunded"

    def turn(client: Any, seen: dict[str, str] | None) -> None:
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "Refund claim 4411."}]
        r = client.chat.completions.create(**request_kwargs("openai", {"messages": msgs, "tools": [_FN]}))
        tc = r.choices[0].message.tool_calls[0]
        args = json.loads(tc.function.arguments)
        result = issue_refund(args["claim_id"], args["amount_usd"], tc.id, seen)
        msgs += [r.choices[0].message.model_dump(exclude_none=True), {"role": "tool", "tool_call_id": tc.id, "content": result}]
        client.chat.completions.create(**request_kwargs("openai", {"messages": msgs, "tools": [_FN]}))

    def run(seen: dict[str, str] | None) -> list[str]:
        srv.state.reset()
        client = make_client("openai", srv.url, pack_id="AG-28", max_retries=0, timeout=0.5)
        call_ids = []
        for _attempt in range(2):  # naive "retry the whole turn on timeout"
            try:
                turn(client, seen)
                break
            except openai.APITimeoutError:
                continue
        call_ids = [e["call_id"] for e in ledger]
        return call_ids

    naive = run(None)
    naive_refunds = len(ledger)
    ledger.clear()
    deduped = run({})
    out = {"naive_refunds": naive_refunds, "naive_call_ids": naive, "dedup_refunds": len(ledger), "dedup_call_ids": deduped}
    out["finding"] = {"cls": "agent.side_effect_replay", "detail": f"timeout after the refund, turn replayed: {naive_refunds} "
                      f"refunds with distinct call_ids {len(set(naive)) == len(naive)}; dedupe on business key "
                      f"(claim+amount): {len(ledger)} refund"}
    return out


_FN = {"type": "function", "function": {"name": "issue_refund", "description": "Refund a claim.",
       "parameters": {"type": "object", "properties": {"claim_id": {"type": "string"}, "amount_usd": {"type": "number"}},
                      "required": ["claim_id", "amount_usd"], "additionalProperties": False}}}

logging.getLogger("openai.agents").setLevel(logging.ERROR)

SCENARIOS = {"AG-22": ag22, "AG-23": ag23, "AG-24": ag24, "AG-26": ag26, "AG-27": ag27, "AG-28": ag28}
