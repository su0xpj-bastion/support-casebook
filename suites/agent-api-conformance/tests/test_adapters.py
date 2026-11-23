"""What each adapter actually puts on the wire, and how it normalizes what comes back."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ace.adapters import ADAPTERS, Result, _args
from ace.cli import cost
from ace.scenarios import WEATHER_TOOL, Outcome
from mockserve import server as mock

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def captured(monkeypatch):
    """Run a real mock, and record every request body it receives."""
    seen: list[tuple[str, dict]] = []
    original = mock.Handler.do_POST

    def spy(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        seen.append((self.path, json.loads(body or b"{}")))
        import io
        self.rfile = io.BytesIO(body)
        return original(self)

    monkeypatch.setattr(mock.Handler, "do_POST", spy)
    srv = mock.serve(0, set())
    yield srv.server_port, seen
    srv.shutdown()


def adapter(proto, port):
    base = f"http://127.0.0.1:{port}" + ("" if proto == "messages" else "/v1")
    return ADAPTERS[proto]("t", "mock-model", base, "test-key", {})


def test_chat_request_shape(captured):
    port, seen = captured
    a = adapter("chat", port)
    a.send(a.new("sys", "What's the weather in Paris?"), tools=[WEATHER_TOOL], max_tokens=50)
    path, body = seen[-1]
    assert path == "/v1/chat/completions"
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["type"] == "function" and body["tools"][0]["function"]["strict"] is True


def test_responses_request_shape_and_tool_result_items(captured):
    port, seen = captured
    a = adapter("responses", port)
    conv = a.new("sys", "What's the weather in Paris?")
    r = a.send(conv, tools=[WEATHER_TOOL], max_tokens=50)
    a.add_tool_results(conv, r, {r.tool_calls[0].id: '{"city":"Paris","temp_c":18,"conditions":"sunny"}'})
    a.send(conv, tools=[WEATHER_TOOL], max_tokens=50)
    path, body = seen[-1]
    assert path == "/v1/responses"
    assert body["instructions"] == "sys" and body["store"] is False and body["max_output_tokens"] == 50
    kinds = [i.get("type", i.get("role")) for i in body["input"]]
    assert kinds == ["user", "function_call", "function_call_output"]
    assert body["tools"][0] == {"type": "function", "name": "get_weather", "description": WEATHER_TOOL["description"],
                                "parameters": WEATHER_TOOL["schema"], "strict": True}


def test_messages_sends_all_tool_results_in_one_user_turn(captured):
    port, seen = captured
    a = adapter("messages", port)
    conv = a.new(None, "What's the weather in Paris and in Tokyo? Check both.")
    r = a.send(conv, tools=[WEATHER_TOOL], max_tokens=100)
    assert len(r.tool_calls) == 2
    a.add_tool_results(conv, r, {c.id: "{}" for c in r.tool_calls})
    a.send(conv, tools=[WEATHER_TOOL], max_tokens=100)
    path, body = seen[-1]
    assert path.startswith("/v1/messages")
    last = body["messages"][-1]
    assert last["role"] == "user" and [b["type"] for b in last["content"]] == ["tool_result", "tool_result"]
    assert "temperature" not in body                      # rejected by current Claude models


def test_messages_cache_flag_sets_top_level_cache_control(captured):
    port, seen = captured
    a = adapter("messages", port)
    a.send(a.new("s", "Reply with exactly: A"), max_tokens=10, cache=True)
    assert seen[-1][1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize("proto", ["chat", "responses", "messages"])
def test_normalized_stop_reasons(captured, proto):
    port, _ = captured
    a = adapter(proto, port)
    assert a.send(a.new(None, "Reply with exactly: A"), max_tokens=10).stop == "end"
    assert a.send(a.new(None, "Write a long story."), max_tokens=5).stop == "length"
    assert a.send(a.new(None, "Weather in Paris?"), tools=[WEATHER_TOOL], max_tokens=50).stop == "tool"


def test_args_parsing():
    assert _args('{"city": "Paris"}') == {"city": "Paris"}
    assert _args('{"city": "Par') is None and _args("[1]") is None


def test_cost_counts_cached_and_written_tokens_at_their_own_rates():
    r = Result(input_tokens=1_000_000, output_tokens=100_000, cached_tokens=400_000, cache_write_tokens=100_000)
    price = {"input": 4.0, "output": 20.0, "cached_input": 0.2, "cache_write": 5.0}
    # 500k uncached * 4 + 400k * 0.2 + 100k * 5 + 100k out * 20  (per million)
    assert cost([Outcome("x", "pass", "", [r])], price) == pytest.approx(2.0 + 0.08 + 0.5 + 2.0)
    assert cost([Outcome("x", "pass", "", [r])], {"input": None, "output": None}) is None


def test_cli_mock_run_writes_a_report(tmp_path):
    out = tmp_path / "r"
    p = subprocess.run([sys.executable, "-m", "ace", "run", "--only", "stream_basic,error_contract", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    data = json.loads((out / "report.json").read_text())
    assert data["meta"]["verdict"] == "PASS" and set(data["results"]) == {"mock-responses", "mock-messages", "mock-chat"}


def test_live_without_keys_skips_every_provider(tmp_path, monkeypatch):
    env = {k: v for k, v in __import__("os").environ.items() if not k.endswith("_API_KEY")}
    p = subprocess.run([sys.executable, "-m", "ace", "run", "--target", "live", "--out", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    assert p.returncode == 2 and "is not set" in p.stderr
