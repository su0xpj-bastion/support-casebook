"""Triage eval: rules vs LLM vs combined, with hard gates.

Gates (CI fails if violated in the committed report):
  false_retryable == 0   no non-retryable ticket (billing, auth, 4xx config) ever gets a retryable verdict
  redaction_leaks == 0   no planted key reaches a prompt or an output

Reported: strict accuracy, safe accuracy (needs_more_info counts as correct where the ticket lacks the
distinguishing evidence), macro-F1 over gold classes, unsupported-quote rate (LLM quotes not found in
the ticket), abstain rate on full-evidence tickets, and the same split train/test by pack family.

  python -m casebook.triage.evaluate --model qwen2.5:1.5b            # local Ollama (free)
  python -m casebook.triage.evaluate --provider openai --model gpt-6-luna   # your key, costs cents
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import openai

from casebook.triage import classify as C
from casebook.triage import dataset as D

OUT = Path(__file__).resolve().parents[2] / "triage" / "eval"


def _f1(pairs: list[tuple[str, str]]) -> float:
    golds = {g for g, _ in pairs}
    scores = []
    for c in golds:
        tp = sum(1 for g, p in pairs if g == c and p == c)
        fp = sum(1 for g, p in pairs if g != c and p == c)
        fn = sum(1 for g, p in pairs if g == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def metrics(results: list[C.Result], tickets: dict[str, D.Ticket]) -> dict[str, Any]:
    rows = [(r, tickets[r.ticket]) for r in results]
    n = len(rows)
    strict = sum(r.label == t.gold for r, t in rows)
    safe = sum(r.label == t.gold or (t.abstain_ok and r.label == "needs_more_info") for r, t in rows)
    false_retry = [t.id for r, t in rows if D.LABELS[r.label] == "Y" and t.gold_retry == "N"]
    wrong_unsafe = [t.id for r, t in rows if r.label not in (t.gold, "needs_more_info")]
    full = [(r, t) for r, t in rows if t.evidence_level == "full"]
    quotes = sum(len(r.evidence) for r, _ in rows)
    unsupported = sum(len(r.unsupported) for r, _ in rows)
    leaks = sum(1 for r, _ in rows if r.prompt_had_secret or r.output_had_secret)
    llm_errors = sum(1 for r, _ in rows if r.basis.startswith("llm error"))
    safe_strict = sum((r.label == t.gold or (t.abstain_ok and r.label == "needs_more_info"))
                      and not r.basis.startswith("llm error") for r, t in rows)
    return {"n": n, "strict_accuracy": round(strict / n, 3), "safe_accuracy": round(safe / n, 3),
            "safe_accuracy_errors_as_failures": round(safe_strict / n, 3),
            "macro_f1": _f1([(t.gold, r.label) for r, t in rows]),
            "false_retryable": len(false_retry), "false_retryable_ids": false_retry,
            "wrong_class_not_abstain": len(wrong_unsafe), "wrong_ids": wrong_unsafe[:20],
            "abstain_rate_full_evidence": round(sum(r.label == "needs_more_info" for r, _ in full) / len(full), 3) if full else None,
            "unsupported_quote_rate": round(unsupported / quotes, 3) if quotes else 0.0,
            "redaction_leaks": leaks, "llm_errors": llm_errors}


def run(provider: str, model: str, limit: int | None = None) -> dict[str, Any]:
    tickets = D.synthetic() + D.adversarial()
    if limit:
        tickets = tickets[:limit] + D.adversarial()
    train, test = D.split(tickets)
    shots = [next(t for t in train if t.family == f and t.evidence_level == "full") for f in ("limits", "auth")]
    shots.append(next(t for t in D.adversarial() if t.gold == "needs_more_info"))
    by_id = {t.id: t for t in tickets}
    client = C.ollama_client() if provider == "ollama" else openai.OpenAI(max_retries=2, timeout=120)
    out: dict[str, Any] = {"provider": provider, "model": model, "run_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "tickets": len(tickets), "train": len(train), "test": len(test),
                           "few_shot_ids": [s.id for s in shots], "few_shot_families": sorted({s.family for s in shots})}
    modes = {"rules": lambda t: C.triage_rules(t), "llm": lambda t: C.triage_llm(t, client, model, shots),
             "combined": lambda t: C.triage_combined(t, client, model, shots)}
    per: dict[str, list[C.Result]] = {}
    for mode, fn in modes.items():
        t0 = time.monotonic()
        per[mode] = [fn(t) for t in tickets if t.id not in out["few_shot_ids"]]
        out[mode] = {"all": metrics(per[mode], by_id),
                     "test_split": metrics([r for r in per[mode] if by_id[r.ticket].family in D.TEST_FAMILIES], by_id),
                     "adversarial": metrics([r for r in per[mode] if by_id[r.ticket].source == "adversarial"], by_id),
                     "seconds": round(time.monotonic() - t0, 1)}
    out["guard_actions"] = Counter(r.basis.split(":")[0] for r in per["combined"] if r.basis.startswith("guard"))
    out["gates"] = {m: {"false_retryable_zero": out[m]["all"]["false_retryable"] == 0,
                        "redaction_leaks_zero": out[m]["all"]["redaction_leaks"] == 0} for m in modes}
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{provider}-{model.replace(':', '_').replace('/', '_')}"
    (OUT / f"results-{tag}.json").write_text(json.dumps(out, indent=2, default=list) + "\n")
    (OUT / f"predictions-{tag}.jsonl").write_text("\n".join(json.dumps({"mode": m, **r.__dict__}) for m, rs in per.items() for r in rs) + "\n")
    _ = defaultdict
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ollama", choices=["ollama", "openai"])
    ap.add_argument("--model", default="qwen2.5:1.5b")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    out = run(a.provider, a.model, a.limit)
    for m in ("rules", "llm", "combined"):
        x = out[m]["all"]
        print(f"{m:9} strict {x['strict_accuracy']:.3f}  safe {x['safe_accuracy']:.3f}  macroF1 {x['macro_f1']:.3f}  "
              f"false-retryable {x['false_retryable']}  wrong {x['wrong_class_not_abstain']}  unsupported {x['unsupported_quote_rate']}  "
              f"leaks {x['redaction_leaks']}  llm-errors {x['llm_errors']}  ({out[m]['seconds']}s)")
    print("gates:", out["gates"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def write_report() -> Path:
    """Render triage/eval/REPORT.md from every results-*.json present."""
    rows = []
    for f in sorted(OUT.glob("results-*.json")):
        r = json.loads(f.read_text())
        if r.get("tickets", 0) < 200:          # skip smoke runs (--limit)
            continue
        rows.append(r)
    lines = ["# Triage eval", "",
             "Generated by `python -m casebook.triage.evaluate`. Tickets: 240 synthetic (10 per error pack: full / partial / vague "
             "evidence, misleading customer framing, planted keys) + 20 hand-written adversarial. Few-shot examples come only from "
             "the train families; billing and provider families are held out.", "",
             "**Hard gates:** `false_retryable == 0` (never tell a customer to retry a billing/auth/config failure) and "
             "`redaction_leaks == 0` (no planted key in any prompt or output). Retryability always comes from the class table, "
             "never from model text.", ""]
    for r in rows:
        lines += [f"## {r['provider']} · `{r['model']}` · {r['run_utc']}", "",
                  "| Mode | Strict acc. | Safe acc. | Safe acc. (errors = failures) | Macro-F1 | False-retryable | Wrong (not abstain) | Unsupported quotes | Leaks | LLM errors | Abstain on full evidence |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for m in ("rules", "llm", "combined"):
            x = r[m]["all"]
            lines.append(f"| {m} | {x['strict_accuracy']:.3f} | {x['safe_accuracy']:.3f} | {x.get('safe_accuracy_errors_as_failures', x['safe_accuracy']):.3f} | {x['macro_f1']:.3f} | **{x['false_retryable']}** | "
                         f"{x['wrong_class_not_abstain']} | {x['unsupported_quote_rate']:.3f} | {x['redaction_leaks']} | {x.get('llm_errors', 0)} | {x['abstain_rate_full_evidence']} |")
        lines += ["", "Held-out families (test split) and adversarial set:", "",
                  "| Mode | Test strict | Test safe | Test false-retryable | Adversarial strict | Adversarial safe | Adversarial false-retryable |",
                  "|---|---|---|---|---|---|---|"]
        for m in ("rules", "llm", "combined"):
            te, ad = r[m]["test_split"], r[m]["adversarial"]
            lines.append(f"| {m} | {te['strict_accuracy']:.3f} | {te['safe_accuracy']:.3f} | {te['false_retryable']} | "
                         f"{ad['strict_accuracy']:.3f} | {ad['safe_accuracy']:.3f} | {ad['false_retryable']} |")
        lines += ["", f"Guard actions in combined mode: {dict(r.get('guard_actions', {}))}", "",
                  f"Gates: {json.dumps(r['gates'])}", ""]
    out = OUT / "REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def rescore(tag: str) -> dict[str, Any]:
    """Recompute a results file from its saved predictions (no model calls) after a metrics change."""
    res_path, pred_path = OUT / f"results-{tag}.json", OUT / f"predictions-{tag}.jsonl"
    out = json.loads(res_path.read_text())
    tickets = {t.id: t for t in D.synthetic() + D.adversarial()}
    per: dict[str, list[C.Result]] = defaultdict(list)
    for line in pred_path.read_text().splitlines():
        row = json.loads(line)
        mode = row["mode"]
        per[mode].append(C.Result(**row))
    for mode, rs in per.items():
        out[mode]["all"] = metrics(rs, tickets)
        out[mode]["test_split"] = metrics([r for r in rs if tickets[r.ticket].family in D.TEST_FAMILIES], tickets)
        out[mode]["adversarial"] = metrics([r for r in rs if tickets[r.ticket].source == "adversarial"], tickets)
    res_path.write_text(json.dumps(out, indent=2, default=list) + "\n")
    return out
