"""Tests for stream_latency.py. No network: the OpenAI client and the clock are faked."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

import stream_latency as sl


# ---------- fakes ----------

def chunk(content=None, finish=None, usage=None, choices=True):
    if not choices:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)


class FakeClock:
    """perf_counter replacement: each call returns the next scripted value."""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return iter(self.chunks)

    def __exit__(self, *exc):
        return False


def _response(status):
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.openai.com/v1/chat/completions"))


def rate_limit():
    return openai.RateLimitError("rate limited", response=_response(429), body=None)


def server_error():
    return openai.InternalServerError("boom", response=_response(500), body=None)


class FakeClient:
    """Each create() call pops the next scripted outcome: a list of chunks or an exception."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeStream(outcome)


@pytest.fixture
def install(monkeypatch):
    sleeps = []

    def _install(outcomes, clock):
        client = FakeClient(outcomes)
        monkeypatch.setattr(sl.openai, "OpenAI", lambda: client)
        monkeypatch.setattr(sl.time, "perf_counter", FakeClock(clock))
        monkeypatch.setattr(sl.time, "sleep", sleeps.append)
        monkeypatch.setattr(sl.random, "random", lambda: 0.5)
        return client, sleeps

    return _install


USAGE = SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15)
HEALTHY = [chunk("Hel"), chunk("lo"), chunk("!"), chunk(finish="stop"), chunk(usage=USAGE, choices=False)]


# ---------- compute_metrics: pure math ----------

def test_metrics_from_timestamps():
    m = sl.compute_metrics(10.0, [10.2, 10.3, 10.5], 10.6)
    assert m.ttft == pytest.approx(0.2)
    assert m.tbt == pytest.approx(0.15)          # gaps 0.1 and 0.2
    assert m.total_latency == pytest.approx(0.6)
    assert m.token_count == 3


def test_no_tokens_reports_zeros_but_keeps_total():
    m = sl.compute_metrics(1.0, [], 1.4)
    assert (m.ttft, m.tbt, m.token_count) == (0.0, 0.0, 0)
    assert m.total_latency == pytest.approx(0.4)


def test_single_token_has_no_gap():
    m = sl.compute_metrics(0.0, [0.25], 0.3)
    assert m.ttft == pytest.approx(0.25)
    assert m.tbt == 0.0


def test_clock_going_backwards_never_reports_negative():
    m = sl.compute_metrics(5.0, [4.9], 4.8)
    assert m.ttft == 0.0 and m.total_latency == 0.0


# ---------- measure_latency: streaming ----------

def test_stream_is_timed_per_content_chunk(install, capsys):
    # perf_counter calls: start, 3 content chunks, end
    client, _ = install([HEALTHY], [100.0, 100.3, 100.35, 100.45, 100.5])
    m = sl.measure_latency("hi")
    assert m.ttft == pytest.approx(0.3)
    assert m.tbt == pytest.approx(0.075)
    assert m.total_latency == pytest.approx(0.5)
    assert m.token_count == 3                    # finish and usage chunks are not tokens
    req = client.calls[0]
    assert req["stream"] is True
    assert req["stream_options"] == {"include_usage": True}
    assert req["model"] == sl.MODEL
    assert "api completion_tokens=3, chunk count=3" in capsys.readouterr().err


def test_usage_chunk_without_choices_does_not_crash(install):
    install([[chunk(usage=USAGE, choices=False)]], [0.0, 0.1])
    assert sl.measure_latency("hi").token_count == 0


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_non_stop_finish_reason_warns_on_stderr(install, capsys, reason):
    install([[chunk("a"), chunk(finish=reason)]], [0.0, 0.1, 0.2])
    sl.measure_latency("hi")
    assert f"[WARN] stream ended with finish_reason='{reason}'" in capsys.readouterr().err


def test_stop_finish_reason_does_not_warn(install, capsys):
    install([HEALTHY], [0.0, 0.1, 0.2, 0.3, 0.4])
    sl.measure_latency("hi")
    assert "[WARN]" not in capsys.readouterr().err


# ---------- retry ----------

def test_429_is_retried_with_jittered_backoff(install):
    client, sleeps = install([rate_limit(), rate_limit(), HEALTHY],
                             [0.0, 1.0, 50.0, 50.2, 50.3, 50.4, 50.5])
    m = sl.measure_latency("hi")
    assert len(client.calls) == 3
    assert sleeps == [1.5, 2.5]                  # 2**0 + 0.5, 2**1 + 0.5


def test_retry_clock_resets_so_backoff_never_inflates_ttft(install):
    # first attempt starts at t=0 and fails; the successful attempt starts at t=50
    install([rate_limit(), HEALTHY], [0.0, 50.0, 50.2, 50.3, 50.4, 50.5])
    m = sl.measure_latency("hi")
    assert m.ttft == pytest.approx(0.2)
    assert m.total_latency == pytest.approx(0.5)


def test_429_on_every_attempt_raises_after_max_attempts(install):
    client, sleeps = install([rate_limit()] * sl.MAX_ATTEMPTS, [0.0] * sl.MAX_ATTEMPTS)
    with pytest.raises(openai.RateLimitError):
        sl.measure_latency("hi")
    assert len(client.calls) == sl.MAX_ATTEMPTS
    assert len(sleeps) == sl.MAX_ATTEMPTS - 1    # no sleep after the final failure


def test_5xx_is_not_retried(install):
    client, sleeps = install([server_error()], [0.0])
    with pytest.raises(openai.InternalServerError):
        sl.measure_latency("hi")
    assert len(client.calls) == 1 and sleeps == []


def test_connection_error_is_not_retried(install):
    err = openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com"))
    client, sleeps = install([err], [0.0])
    with pytest.raises(openai.APIConnectionError):
        sl.measure_latency("hi")
    assert len(client.calls) == 1 and sleeps == []


# ---------- CLI ----------

def test_cli_prints_json_metrics(install, monkeypatch, capsys):
    install([HEALTHY], [0.0, 0.1, 0.2, 0.3, 0.4])
    monkeypatch.setattr(sl, "load_dotenv", lambda: None)
    monkeypatch.setattr(sl.sys, "argv", ["stream_latency.py", "hi"])
    sl.main()
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"ttft", "tbt", "total_latency", "token_count"}
    assert out["token_count"] == 3


def test_cli_rate_limit_exits_1_with_clean_message(install, monkeypatch, capsys):
    install([rate_limit()] * sl.MAX_ATTEMPTS, [0.0] * sl.MAX_ATTEMPTS)
    monkeypatch.setattr(sl, "load_dotenv", lambda: None)
    monkeypatch.setattr(sl.sys, "argv", ["stream_latency.py", "hi"])
    with pytest.raises(SystemExit) as exc:
        sl.main()
    assert exc.value.code == 1
    assert "[ERROR] rate limit exceeded after 3 attempts" in capsys.readouterr().err


def test_cli_status_error_reports_code(install, monkeypatch, capsys):
    install([server_error()], [0.0])
    monkeypatch.setattr(sl, "load_dotenv", lambda: None)
    monkeypatch.setattr(sl.sys, "argv", ["stream_latency.py", "hi"])
    with pytest.raises(SystemExit):
        sl.main()
    assert "[ERROR] status 500" in capsys.readouterr().err


def test_cli_requires_exactly_one_prompt(monkeypatch):
    monkeypatch.setattr(sl, "load_dotenv", lambda: None)
    monkeypatch.setattr(sl.sys, "argv", ["stream_latency.py"])
    with pytest.raises(SystemExit, match="usage"):
        sl.main()
