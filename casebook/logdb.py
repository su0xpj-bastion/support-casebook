"""Load the casebook's evidence into SQLite so support questions can be answered with SQL.

    python -m casebook sql load                 # cases, triage results, live captures (no network)
    python -m casebook sql load --repro         # + run every error pack against the mock and record request traces
    python -m casebook sql list                 # saved queries in sql/queries/
    python -m casebook sql query wasted_retries
    python -m casebook sql query request_trace --param pack=AN-11

Tables (schema in sql/schema.sql):
  packs          one row per case: provider, status, error code, expected verdict, retryable, owner
  tickets        the 260 triage-eval tickets: family, evidence level, gold class
  predictions    every triage answer per model and mode (rules, llm, combined)
  live_captures  responses captured from the real APIs, with field-by-field match counts
  repro_runs     one row per (pack, mode) repro: requests sent, exception, verdict, provider request id
  requests       every HTTP request the mock received during those runs: request id, retry count, client id
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "schema.sql"
QUERIES = ROOT / "sql" / "queries"
DEFAULT_DB = ROOT / ".ci" / "casebook.db"


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def create(con: sqlite3.Connection) -> None:
    with SCHEMA.open("r", encoding="utf-8") as fh:
        con.executescript(fh.read())


def _error_fields(provider: str, expected: dict[str, Any]) -> tuple[str | None, str | None]:
    body = expected.get("body") if isinstance(expected.get("body"), dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    code = err.get("code") if provider == "openai" else details.get("error_code")
    return err.get("type"), code


def load_packs(con: sqlite3.Connection) -> int:
    from casebook import packs
    rows = []
    for p in packs.load_all().values():
        m, exp = p.meta, p.expected
        etype, code = _error_fields(p.provider, exp)
        verdict = m.get("verdict") or {}
        rows.append((p.id, p.provider, m.get("surface"), m.get("kind", "error"), m.get("title"),
                     exp.get("status") if isinstance(exp.get("status"), int) else None, etype, code,
                     verdict.get("cls"), str(m.get("retry")), verdict.get("owner"), p.provenance))
    con.executemany("INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load_tickets_and_predictions(con: sqlite3.Connection) -> tuple[int, int]:
    from casebook.triage import dataset as D
    tickets = D.synthetic() + D.adversarial()
    con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (t.id, t.provider, t.family, t.source, t.evidence_level, t.gold, t.gold_retry, t.pack,
         int(t.planted_secret), int(t.abstain_ok)) for t in tickets])
    n = 0
    for f in sorted((ROOT / "triage" / "eval").glob("predictions-*.jsonl")):
        model = f.stem.removeprefix("predictions-")
        rows = []
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                rows.append((model, r["mode"], r["ticket"], r["label"], r["retry"], r["basis"],
                             len(r.get("unsupported") or []), int(bool(r.get("prompt_had_secret") or r.get("output_had_secret"))),
                             int(str(r["basis"]).startswith("llm error"))))
        con.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    return len(tickets), n


def load_live_captures(con: sqlite3.Connection) -> int:
    rows = []
    for f in sorted((ROOT / "faults").glob("*/*/live/*.json")):
        with f.open("r", encoding="utf-8") as fh:
            cap = json.load(fh)
        diff = cap.get("diff") or []
        rows.append((cap["pack"], cap["captured_utc"], cap["status"], cap.get("x_should_retry"), cap.get("exception"),
                     (cap.get("verdict") or {}).get("cls"), sum(bool(d["match"]) for d in diff), len(diff)))
    con.executemany("INSERT INTO live_captures VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def record_repro(con: sqlite3.Connection, pack_ids: Iterable[str] | None = None) -> int:
    """Run error packs through the real SDKs against the mock, both ways, and record what was sent."""
    from casebook import packs, repro
    from casebook.mockserver import MockServer
    ids = sorted(pack_ids or [p for p, v in packs.load_all().items() if v.meta.get("kind", "error") == "error"])
    run_id = time.strftime("%Y%m%dT%H%M%S")
    n = 0
    with MockServer() as srv:
        for pid in ids:
            for mode in ("sdk-default", "policy"):
                r = repro.run(pid, srv, mode, sleep=lambda _s: None)
                pkt = r.get("packet") or {}
                verdict = r.get("verdict") or {}
                con.execute("INSERT INTO repro_runs VALUES (?,?,?,?,?,?,?,?)", (
                    run_id, pid, mode, r.get("requests_sent"), r.get("exception"), verdict.get("cls"),
                    verdict.get("retryable"), (pkt.get("request_ids") or {}).get("provider")))
                for seq, q in enumerate(x for x in srv.state.requests if x["key"] == pid):
                    h = q["headers"]
                    con.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)", (
                        run_id, pid, mode, seq + 1, q["path"], q["request_id"] or None,
                        int(h.get("x-stainless-retry-count", 0) or 0), h.get("x-client-request-id")))
                    n += 1
    con.commit()
    return n


def load(path: Path = DEFAULT_DB, *, repro_packs: Iterable[str] | None = None, with_repro: bool = False) -> dict[str, int]:
    if path.exists():
        path.unlink()
    con = connect(path)
    create(con)
    counts = {"packs": load_packs(con)}
    counts["tickets"], counts["predictions"] = load_tickets_and_predictions(con)
    counts["live_captures"] = load_live_captures(con)
    con.commit()
    if with_repro or repro_packs:
        counts["requests"] = record_repro(con, repro_packs)
    con.close()
    return counts


def saved_queries() -> dict[str, Path]:
    return {p.stem: p for p in sorted(QUERIES.glob("*.sql"))}


def run_query(name: str, params: dict[str, str] | None = None, path: Path = DEFAULT_DB) -> list[sqlite3.Row]:
    q = saved_queries()[name]
    con = connect(path)
    with q.open("r", encoding="utf-8") as fh:
        rows = con.execute(fh.read(), params or {}).fetchall()
    con.close()
    return rows


def describe(name: str) -> str:
    """First comment line of a saved query: what support question it answers."""
    with saved_queries()[name].open("r", encoding="utf-8") as fh:
        first = fh.readline().strip()
    return first.removeprefix("--").strip()


def table(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(no rows)"
    cols = rows[0].keys()
    data = [[("" if r[c] is None else str(r[c])) for c in cols] for r in rows]
    widths = [max(len(c), *(len(d[i]) for d in data)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths))
    return "\n".join([line(list(cols)), line(["-" * w for w in widths]), *(line(d) for d in data)])
