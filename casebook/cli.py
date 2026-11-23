"""casebook CLI.

  python -m casebook packs                 list packs with tier, retry verdict, provenance
  python -m casebook repro AN-01 [--json]  run one pack (or "all") both ways against the mock
  python -m casebook redact FILE|-         redact a ticket/log and print the minimal packet
  python -m casebook live OA-07 --yes      capture the real API response for a pack and diff it
  python -m casebook exposure DOSSIER      dated breaks in an account's code (+ --draft note)
"""
from __future__ import annotations

import argparse
import json
import sys

from casebook import packet as pk
from casebook import packs, repro, rules, scenarios
from casebook.mockserver import MockServer


def _cmd_packs(_: argparse.Namespace) -> int:
    for p in packs.load_all().values():
        print(f"{p.id:6} {p.meta['tier']:3} retry={p.meta['retry']}  {p.provenance:13} {p.meta['title']}")
    return 0


def _cmd_repro(args: argparse.Namespace) -> int:
    allp = packs.load_all()
    ids = list(allp) if args.pack == "all" else [args.pack]
    rows, others = [], []
    sleep = (lambda _s: None) if args.fast else __import__("time").sleep
    with MockServer() as srv:
        for pid in ids:
            if allp[pid].meta.get("kind", "error") != "error":
                others.append(scenarios.run(pid, srv))
                continue
            sdk = repro.run(pid, srv, "sdk-default")
            pol = repro.run(pid, srv, "policy", sleep=sleep)
            rows.append((sdk, pol))
    if args.json:
        print(json.dumps({"errors": [{"sdk_default": a, "policy": b} for a, b in rows], "other": others},
                         indent=2, default=str))
        return 0
    if rows:
        print(f"{'pack':6} {'status':6} {'code':36} {'verdict':28} {'retry':5} {'sdk-default':>11} {'policy':>6}  exception")
    for sdk, pol in rows:
        p, v = sdk["packet"], sdk["verdict"]
        code = p["error"]["code"] or p["error"]["details_error_code"] or p["error"]["type"] or "-"
        status = p["http"]["status"] if p["http"]["status"] is not None else "-"
        pol_n = pol["requests_sent"] if pol["requests_sent"] is not None else "n/a"
        print(f"{sdk['pack']:6} {status!s:<6} {code:36} {v['cls']:28} {v['retryable']:5} "
              f"{sdk['requests_sent']:>11} {pol_n!s:>6}  {sdk['exception']}")
    if others:
        print(f"\n{'pack':6} {'requests':>8}  finding")
    for o in others:
        f = o.get("finding")
        detail = f"{f['cls']}: {f['detail']}" if f else (
            f"{o['exception']} before send: {o['message'][:70]}" if o.get("exception") else "no finding")
        print(f"{o['pack']:6} {o['requests_sent']:>8}  {detail}")
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    from casebook import live

    if not args.yes:
        print("live captures send real requests to api.openai.com / api.anthropic.com; re-run with --yes")
        return 2
    for pid in args.packs:
        cap = live.capture(pid, keyed=args.keyed)
        print(f"{pid}: live {cap['status']} {cap['exception']}  x-should-retry={cap['x_should_retry']}")
        for row in cap["diff"]:
            mark = "=" if row["match"] else "≠"
            print(f"   {mark} {row['field']:20} expected={str(row['expected'])[:60]!s:62} live={str(row['live'])[:80]}")
    return 0


def _cmd_exposure(args: argparse.Namespace) -> int:
    from datetime import date
    from pathlib import Path

    import yaml

    from casebook import exposure

    as_of = date.fromisoformat(args.as_of)
    for d in args.dossiers:
        path = Path(d)
        res = exposure.scan(path, as_of)
        if args.json:
            print(json.dumps(res, indent=2))
            continue
        print(f"{res['account']}  (as of {res['as_of']}, {res['files_scanned']} files)")
        for f in res["findings"]:
            print(f"  {f['severity']} {f['deadline'] or '-':10} {f['pack']:6} {f['where']:58} {f['evidence'][:90]}")
        if args.draft:
            dossier = yaml.safe_load(path.read_text(encoding="utf-8"))
            out = Path("drafts") / f"proactive-{path.stem}-{res['as_of']}.md"
            out.parent.mkdir(exist_ok=True)
            out.write_text(exposure.draft_note(res, dossier), encoding="utf-8")
            print(f"  draft -> {out} (not sent)")
    return 0


def _cmd_redact(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    p = pk.from_text(text, provider=args.provider)
    print(json.dumps({"packet": p, "verdict": rules.classify(p).as_dict()}, indent=2))
    return 0


def _cmd_sql_load(args: argparse.Namespace) -> int:
    from pathlib import Path

    from casebook import logdb
    db = Path(args.db) if args.db else logdb.DEFAULT_DB
    print(json.dumps(logdb.load(db, with_repro=args.repro)), "->", db)
    return 0


def _cmd_sql_list(_: argparse.Namespace) -> int:
    from casebook import logdb
    for name in logdb.saved_queries():
        print(f"{name:28} {logdb.describe(name)}")
    return 0


def _cmd_sql_query(args: argparse.Namespace) -> int:
    from pathlib import Path

    from casebook import logdb
    params = dict(p.split("=", 1) for p in args.param)
    print(logdb.table(logdb.run_query(args.name, params, Path(args.db) if args.db else logdb.DEFAULT_DB)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="casebook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("packs").set_defaults(fn=_cmd_packs)
    r = sub.add_parser("repro")
    r.add_argument("pack")
    r.add_argument("--json", action="store_true")
    r.add_argument("--fast", action="store_true", help="skip the policy wrapper's backoff sleeps (SDK sleeps are real)")
    r.set_defaults(fn=_cmd_repro)
    lv = sub.add_parser("live", help="capture real API responses (free packs need no key)")
    lv.add_argument("packs", nargs="+")
    lv.add_argument("--yes", action="store_true", help="confirm sending real requests")
    lv.add_argument("--keyed", action="store_true", help="allow packs that use your own API key")
    lv.set_defaults(fn=_cmd_live)
    ex = sub.add_parser("exposure", help="scan a dossier's code for dated breaks and draft a proactive note")
    ex.add_argument("dossiers", nargs="+")
    ex.add_argument("--as-of", default="2026-11-19")
    ex.add_argument("--draft", action="store_true", help="write drafts/proactive-<account>-<date>.md")
    ex.add_argument("--json", action="store_true")
    ex.set_defaults(fn=_cmd_exposure)
    rb = sub.add_parser("runbooks", help="generate runbooks/ from every pack")
    rb.set_defaults(fn=lambda _a: print(f"{len(__import__('casebook.gen.runbooks', fromlist=['generate']).generate())} runbooks -> runbooks/") or 0)
    d = sub.add_parser("redact")
    d.add_argument("file")
    d.add_argument("--provider", choices=["openai", "anthropic"])
    d.set_defaults(fn=_cmd_redact)
    from casebook.ci.cli import add_parser as _add_ci
    _add_ci(sub)
    sq = sub.add_parser("sql", help="load evidence into SQLite and run saved support queries")
    sqs = sq.add_subparsers(dest="sql_cmd", required=True)
    sl = sqs.add_parser("load")
    sl.add_argument("--repro", action="store_true", help="also run every error pack against the mock and record requests")
    sl.add_argument("--db")
    sl.set_defaults(fn=_cmd_sql_load)
    sqs.add_parser("list").set_defaults(fn=_cmd_sql_list)
    sr = sqs.add_parser("query")
    sr.add_argument("name")
    sr.add_argument("--param", action="append", default=[], help="name=value for :name in the query")
    sr.add_argument("--db")
    sr.set_defaults(fn=_cmd_sql_query)
    args = ap.parse_args(argv)
    return args.fn(args)
