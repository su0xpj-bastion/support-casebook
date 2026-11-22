"""Sim 1 — provider outage. Can support say 'platform-side' from client signals alone, and how fast?

Template: OpenAI 2024-12-11 (API, ChatGPT and platform login down together; connection-level
failures; status posted ~1 min after impact). The mock drops connections on the API and 503s the
model-listing endpoint (the 'dashboard/login' stand-in) during the window. Ten real OpenAI SDK
clients keep working; one of them has a revoked key (the customer-side control). Every failure is a
Support Packet; the classifier sees only those packets. Updates are generated every UPDATE_EVERY_S
(compressed: 1 s stands for 10 minutes, so 3 s = the 30-minute P1 cadence).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

import openai

from casebook import packet as pk
from casebook import packs
from casebook.gen.updates import customer_update, lint_update
from casebook.mockserver import MockServer
from casebook.repro import make_client, request_kwargs

CLIENTS = 10
RUN_S = 12.0
MIN_PER_S = 10                     # compression: 1 simulated second = 10 minutes of the real event
STATUS_POST_DELAY_S = 0.1          # status page 'Investigating' ~1 minute after impact (template write-up)
UPDATE_EVERY_S = 3.0               # = 30 minutes: P1 update cadence


def classify(window: list[dict[str, Any]]) -> dict[str, Any]:
    """Provider-side if, inside the window, >=60% of distinct clients see connection errors or 5xx on
    >=2 endpoints and none of those failures are auth/billing 4xx."""
    failing = [p for p in window if p["failed"]]
    if not failing:
        return {"verdict": "healthy"}
    clients = {p["client"] for p in window}
    conn_or_5xx = [p for p in failing if p["kind"] in ("connection", "5xx")]
    bad_clients = {p["client"] for p in conn_or_5xx}
    endpoints = {p["endpoint"] for p in conn_or_5xx}
    four_xx = {p["client"] for p in failing if p["kind"] == "4xx"}
    if len(bad_clients) >= 0.6 * len(clients) and len(endpoints) >= 2:
        return {"verdict": "provider-side", "clients_affected": len(bad_clients), "of": len(clients),
                "endpoints": sorted(endpoints), "customer_side_clients": sorted(four_xx)}
    if four_xx:
        return {"verdict": "customer-side", "clients": sorted(four_xx)}
    return {"verdict": "inconclusive", "clients_affected": len(bad_clients), "of": len(clients)}


def _client_loop(i: int, srv: MockServer, t0: float, log: list[dict[str, Any]], lock: threading.Lock) -> None:
    key = "sk-proj-dead" if i == 0 else f"sk-proj-live-{i}"
    client = openai.OpenAI(api_key=key, base_url=f"{srv.url}/v1", max_retries=0, timeout=2,
                           default_headers={"X-Casebook-Pack": "SIM-1"})
    while time.monotonic() - t0 < RUN_S:
        for endpoint in ("chat", "models"):
            ts = time.monotonic() - t0
            rec = {"t": round(ts, 2), "client": i, "endpoint": endpoint, "failed": False, "kind": None}
            try:
                if endpoint == "chat":
                    client.chat.completions.create(**request_kwargs("openai"))
                else:
                    client.models.list()
            except openai.APIConnectionError as e:
                rec.update(failed=True, kind="connection", packet=pk.from_client_error("openai", e, "connection_error"))
            except openai.APIStatusError as e:
                rec.update(failed=True, kind="5xx" if e.status_code >= 500 else "4xx",
                           packet=pk.from_sdk_error("openai", e))
            with lock:
                log.append(rec)
        time.sleep(0.25)


def run() -> dict[str, Any]:
    exp = packs.get("SIM-1").expected
    start, end = float(exp["outage_start_s"]), float(exp["outage_end_s"])
    with MockServer() as srv:
        log: list[dict[str, Any]] = []
        lock = threading.Lock()
        t0 = time.monotonic()
        threads = [threading.Thread(target=_client_loop, args=(i, srv, t0, log, lock)) for i in range(CLIENTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    log.sort(key=lambda r: r["t"])
    timeline: list[dict[str, Any]] = []
    first_provider = None
    pre_verdicts = set()
    t = 0.5
    while t <= RUN_S:
        window = [r for r in log if t - 1.0 <= r["t"] < t]
        v = classify(window)
        timeline.append({"t": round(t, 1), **v})
        if v["verdict"] == "provider-side" and first_provider is None and t >= start:
            first_provider = round(t - start, 1)
        if t < start:
            pre_verdicts.add(v["verdict"])
        t += 0.5
    status_post = STATUS_POST_DELAY_S
    sim_epoch = dt.datetime(2024, 12, 11, 23, 13, tzinfo=dt.timezone.utc)   # 3:13 PM PST, start of the template incident
    updates, lint = [], []
    u_t = start + (first_provider or 0)
    while u_t < end + UPDATE_EVERY_S:
        now = sim_epoch + dt.timedelta(minutes=u_t * MIN_PER_S)
        resolved = u_t >= end
        text = customer_update(
            title="API requests failing across all keys", severity="P1",
            facts=[f"Connection failures on chat completions and model listing from {timeline[-1].get('of', CLIENTS)} independent "
                   f"clients since {(sim_epoch + dt.timedelta(minutes=start * MIN_PER_S)):%H:%M} UTC; no 4xx except one unrelated revoked key",
                   "Failures are connection-level (no HTTP response), consistent with a platform-side incident"],
            impact="All API calls and dashboard sign-in may fail; requests in flight may not complete." if not resolved
            else "Service has recovered for new requests; requests made during the window may need to be re-sent.",
            mitigation="We have escalated to the platform incident channel and are tracking the status page." if not resolved
            else "Recovery confirmed from the same client signals.",
            owner="Support incident commander (casebook)", next_update=None if resolved else now + dt.timedelta(minutes=30),
            unknowns=[] if resolved else ["root cause", "time to recovery"], retryable="Y" if resolved else "C", now=now,
            resolved=resolved)
        updates.append({"t": round(u_t, 1), "text": text})
        lint.append(lint_update(text, severity="P1"))
        u_t += UPDATE_EVERY_S
    first_clear = next((x["t"] - end for x in timeline if x["t"] >= end and x["verdict"] != "provider-side"), None)
    out = {"seconds_to_provider_side": first_provider, "status_page_post_s": status_post,
           "minutes_to_provider_side": round(first_provider * MIN_PER_S) if first_provider is not None else None,
           "minutes_to_see_recovery": round(first_clear * MIN_PER_S) if first_clear is not None else None,
           "lint": lint,
           "verdicts_before_outage": sorted(pre_verdicts), "updates": len(updates), "lint_problems": sum(map(len, lint)),
           "timeline": timeline, "first_update": updates[0]["text"] if updates else None,
           "requests": len(log), "control_client_4xx": sum(1 for r in log if r["client"] == 0 and r["kind"] == "4xx")}
    out["finding"] = {"cls": "sim.provider_outage", "detail": (
        f"classified provider-side {out['minutes_to_provider_side']} min after impact from client signals alone (status page: ~1 min); "
        f"recovery visible {out['minutes_to_see_recovery']} min after it happened; "
        f"before the outage the revoked-key client alone -> {sorted(pre_verdicts)}; {len(updates)} P1 updates generated, "
        f"lint problems {out['lint_problems']}")}
    return out
