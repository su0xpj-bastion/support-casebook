"""Generate one runbook per pack from pack.yaml, fix.md, expected.json and any live capture.

A runbook is the support-facing version of a pack: symptom, how to confirm, what to tell the
customer, when to escalate, and how sure we are (provenance)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from casebook import packs

OUT = Path(__file__).resolve().parents[2] / "runbooks"


def _escalate_when(meta: dict[str, Any]) -> str:
    owner = (meta.get("verdict") or {}).get("owner")
    if owner == "provider" or meta.get("kind") == "sim":
        return "Escalate to the provider/platform incident channel with request IDs, UTC window and blast radius once two or more customers or keys show it."
    if owner == "customer-admin":
        return "Not a platform bug: route to the customer's admin (and the account team for tier/billing changes). Escalate only if the documented fix doesn't change the evidence."
    return "Escalate to Engineering only if the documented fix doesn't change the evidence; include the full packet and a minimal repro."


def render(p: packs.Pack) -> str:
    m, exp = p.meta, p.expected
    fix = (p.path / "fix.md").read_text(encoding="utf-8").split("\n", 1)[1].strip()
    live = sorted((p.path / "live").glob("*.json")) if (p.path / "live").is_dir() else []
    lines = [f"# {m['id']} — {m['title']}", "",
             f"*Provider:* {m['provider']} · *surface:* {m['surface']} · *kind:* {m.get('kind', 'error')} · "
             f"*retry:* {m['retry']} · *weakest provenance:* `{p.provenance}`", ""]
    lines += ["## Confirm it", *[f"- {e}" for e in m.get("evidence_required", [])]]
    if m.get("looks_like"):
        lines += ["", "Easily confused with: " + ", ".join(str(x) for x in m["looks_like"])]
    lines += ["", "## Fix and customer guidance", fix, "", "## Escalate when", _escalate_when(m), ""]
    if m.get("live_findings"):
        lines += ["## Live findings", *[f"- {f['date']}: {f['observed']}" for f in m["live_findings"]], ""]
    if live:
        cap = json.loads(live[-1].read_text())
        lines += [f"Latest live capture: {cap['captured_utc']} — status {cap['status']}, x-should-retry={cap['x_should_retry']}", ""]
    lines += ["## Provenance", *[f"- {k}: {v}" for k, v in exp.get("provenance", {}).items()], "",
              f"Reproduce: `python -m casebook repro {m['id']}`", ""]
    return "\n".join(lines)


def generate(out: Path = OUT) -> list[Path]:
    out.mkdir(exist_ok=True)
    written = []
    for p in packs.load_all().values():
        f = out / f"{p.id}.md"
        f.write_text(render(p), encoding="utf-8")
        written.append(f)
    idx = ["# Runbooks", "", "| ID | Title | Retry | Provenance |", "|---|---|---|---|"]
    idx += [f"| [{p.id}]({p.id}.md) | {p.meta['title']} | {p.meta['retry']} | {p.provenance} |" for p in packs.load_all().values()]
    (out / "README.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    return written
