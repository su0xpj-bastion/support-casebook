"""Sim 2 — a capacity incident amplified by client retries (and OA-03, 'retries make it worse').

Template: OpenAI's 2026-04-20 write-up, where an overloaded auth dependency was made worse by
retries. Here: the mock's capacity drops for INCIDENT_S seconds, then recovers. A fleet of real
OpenAI SDK clients keeps sending the same logical demand under three strategies:

  naive        app retries immediately, up to 10 attempts (SDK retries off)
  sdk_default  official SDK defaults (2 retries, short exponential backoff)
  budgeted     SDK retries off; jittered backoff; a global retry budget (retries <= 10% of calls)

Measured per strategy: amplification (requests sent / logical calls), success rate, and recovery
time after capacity returns. Time is compressed (seconds stand for minutes); the shape is the point.
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import openai

from casebook.mockserver import MockServer
from casebook.repro import make_client, request_kwargs

WORKERS = 20
CALL_EVERY_S = 0.5          # demand: 40 logical calls/s
INCIDENT_S = 3.0            # capacity 10 rps during the incident
RUN_S = 9.0                 # then 60 rps
SCHEDULE = [{"until_s": INCIDENT_S, "rps": 10}, {"until_s": 10_000, "rps": 60}]


@dataclass
class Budget:
    ratio: float = 0.1
    window_s: float = 1.0
    calls: deque = field(default_factory=deque)
    retries: deque = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def note_call(self, t: float) -> None:
        with self.lock:
            self.calls.append(t)

    def allow_retry(self, t: float) -> bool:
        with self.lock:
            for q in (self.calls, self.retries):
                while q and t - q[0] > self.window_s:
                    q.popleft()
            if len(self.retries) < max(1, self.ratio * len(self.calls)):
                self.retries.append(t)
                return True
            return False


def _worker(strategy: str, srv: MockServer, t0: float, results: list[dict[str, Any]], budget: Budget, seed: int) -> None:
    rnd = random.Random(seed)
    client = make_client("openai", srv.url, pack_id="SIM-2", max_retries=2 if strategy == "sdk_default" else 0, timeout=5)
    kw = request_kwargs("openai")
    next_at = t0 + rnd.random() * CALL_EVERY_S
    while next_at < t0 + RUN_S:
        time.sleep(max(0.0, next_at - time.monotonic()))
        start = time.monotonic()
        budget.note_call(start)
        ok = False
        attempt = 0
        while True:
            try:
                client.chat.completions.create(**kw)
                ok = True
                break
            except openai.APIStatusError:
                attempt += 1
                if strategy == "naive" and attempt < 10:
                    continue
                if strategy == "budgeted" and attempt < 4 and budget.allow_retry(time.monotonic()):
                    time.sleep(0.2 * 2 ** (attempt - 1) + rnd.random() * 0.2)
                    continue
                break
            except openai.APIConnectionError:
                break
        results.append({"start": start - t0, "end": time.monotonic() - t0, "ok": ok})
        next_at += CALL_EVERY_S


def run_strategy(strategy: str) -> dict[str, Any]:
    with MockServer() as srv:
        results: list[dict[str, Any]] = []
        budget = Budget()
        t0 = time.monotonic()
        threads = [threading.Thread(target=_worker, args=(strategy, srv, t0, results, budget, i)) for i in range(WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sent = srv.state.hits.get("SIM-2", 0)
    calls = len(results)
    ok = sum(r["ok"] for r in results)
    # recovery: first 1 s bucket after the incident where >= 95% of calls that started in it succeeded
    recovery = None
    for b in range(int(INCIDENT_S), int(RUN_S)):
        bucket = [r for r in results if b <= r["start"] < b + 1]
        if bucket and sum(r["ok"] for r in bucket) / len(bucket) >= 0.95:
            recovery = round(b - INCIDENT_S, 1)
            break
    after = [r for r in results if r["start"] >= INCIDENT_S]
    return {"strategy": strategy, "logical_calls": calls, "requests_sent": sent,
            "amplification": round(sent / calls, 2) if calls else None,
            "success_rate": round(ok / calls, 3) if calls else None,
            "success_rate_after_capacity_restored": round(sum(r["ok"] for r in after) / len(after), 3) if after else None,
            "recovery_s_after_capacity_restored": recovery}


def run() -> dict[str, Any]:
    out = {s: run_strategy(s) for s in ("naive", "sdk_default", "budgeted")}
    n, d, b = out["naive"], out["sdk_default"], out["budgeted"]

    def rec(r: dict[str, Any]) -> str:
        v = r["recovery_s_after_capacity_restored"]
        return f"{v}s" if v is not None else f"never (in {RUN_S - INCIDENT_S:.0f}s)"

    out["finding"] = {"cls": "sim.retry_amplification", "detail": (
        f"same demand, capacity 10 rps for {INCIDENT_S:.0f}s then 60 rps: amplification naive {n['amplification']}x, "
        f"SDK default {d['amplification']}x, budgeted {b['amplification']}x; recovery after capacity returned: "
        f"naive {rec(n)}, SDK {rec(d)}, budgeted {rec(b)}; overall success naive {n['success_rate']}, "
        f"SDK {d['success_rate']}, budgeted {b['success_rate']} (the budget trades incident-time success for faster recovery)")}
    return out
