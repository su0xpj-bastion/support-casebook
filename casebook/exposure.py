"""Deprecation and configuration exposure scan for a named account.

Reads a dossier (cases/dossiers/<account>.yaml) and the code paths it lists, finds what will
break (or already has) against the published lifecycle and capability facts in casebook.models,
and drafts a dated, proactive note for the account team. Drafts only: nothing is sent.

    python -m casebook exposure cases/dossiers/acme-insurer.yaml --as-of 2026-11-19
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from casebook import models

ROOT = Path(__file__).resolve().parent.parent
SCAN_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".yaml", ".yml", ".json", ".toml", ".env.example", ".txt"}
MODEL_RE = re.compile(r"\b((?:claude|gpt)-[A-Za-z0-9.\-]+)")


@dataclass
class Exposure:
    rule: str
    pack: str
    severity: str           # P1 broken now / <=14 days, P2 <=60 days or certain break on upgrade, P3 hygiene
    deadline: str | None    # ISO date the break happens, if dated
    where: str              # file:line or dossier field
    evidence: str
    fix: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sev_for_date(when: date, today: date) -> str:
    days = (when - today).days
    return "P1" if days <= 14 else ("P2" if days <= 60 else "P3")


def scan(dossier_path: Path, as_of: date = models.AS_OF) -> dict[str, Any]:
    with dossier_path.open("r", encoding="utf-8") as fh:
        dossier = yaml.safe_load(fh)
    findings: list[Exposure] = []
    files = _files(dossier_path.parent, dossier.get("code_paths", []))
    for path in files:
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        file_models = set(MODEL_RE.findall(text))
        for n, line in enumerate(lines, 1):
            context = "\n".join(lines[max(0, n - 4): n])  # this line and the three before it
            findings += _line_rules(line, f"{rel}:{n}", file_models, dossier, as_of, context)
        findings += _file_rules(text, rel, file_models)
    findings += _dossier_rules(dossier, as_of)
    findings = _dedupe(findings)
    order = {"P1": 0, "P2": 1, "P3": 2}
    findings.sort(key=lambda f: (order[f.severity], f.deadline or "9999", f.where))
    return {"account": dossier["account"], "as_of": as_of.isoformat(), "files_scanned": len(files),
            "findings": [f.as_dict() for f in findings]}


def _files(base: Path, code_paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for cp in code_paths:
        p = (base / cp).resolve()
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and (q.suffix in SCAN_SUFFIXES or q.name.endswith(".env.example")))
    return out


def _line_rules(line: str, where: str, file_models: set[str], dossier: dict[str, Any], today: date,
                context: str = "") -> list[Exposure]:
    out: list[Exposure] = []
    for m in MODEL_RE.findall(line):
        r = models.retirement_for(m)
        if r is not None:
            pack = "AN-26" if r.provider == "anthropic" else "OA-24"
            state = "retired" if today >= r.retires else f"retires in {(r.retires - today).days} days"
            repl = f"move to {r.replacement}" if r.replacement else "migrate to a current model"
            sev = "P1" if today >= r.retires else _sev_for_date(r.retires, today)
            out.append(Exposure("model.retirement", pack, sev, r.retires.isoformat(), where,
                                f"{m}: {state} ({r.retires.isoformat()})", f"{repl}. {r.note}".strip()))
        if m in models.WATCH:
            out.append(Exposure("model.watch", "AN-26", "P3", None, where, f"{m}: {models.WATCH[m]}",
                                "Plan the move now; dates are 'not sooner than' notices."))
    newest = file_models & models.NO_FORCED_TOOL_CHOICE
    if newest and re.search(r"""tool_choice\s*[=:]\s*\{?\s*["']type["']\s*:\s*["'](any|tool)["']""", line):
        out.append(Exposure("tools.forced_tool_choice", "AN-19", "P2", None, where,
                            f"forced tool_choice with {sorted(newest)[0]} -> 400 on this model",
                            "Use tool_choice auto with strict: true on the tool, or structured outputs."))
    if any(models.no_thinking_enabled(m) for m in file_models) and re.search(r"""["']type["']\s*:\s*["']enabled["']""", line) \
            and "thinking" in line:
        out.append(Exposure("thinking.enabled", "AN-18", "P2", None, where, "thinking.type enabled on a 4.7+/5.x model -> 400",
                            "Use thinking.type adaptive and output_config.effort."))
    if "computer_20251124" in line:
        out.append(Exposure("tools.computer_version", "AN-18", "P2", None, where, "computer_20251124 tool type",
                            f"Use {models.COMPUTER_TOOL_CURRENT} on Opus/Sonnet 5.x."))
    if re.search(r"\b(temperature|top_p|top_k)\s*=", line) and any(m.startswith("claude") for m in file_models):
        out.append(Exposure("params.sampling", "AN-21", "P2", None, where,
                            "sampling parameter in a Claude call: TypeError on anthropic>=2 SDK, 400 under API version 2026-10-21",
                            "Remove temperature/top_p/top_k before upgrading the SDK."))
    if re.search(r"\.beta\.assistants|/v1/assistants|beta\.threads", line):
        out.append(Exposure("api.assistants", "OA-24", "P1", "2026-08-26", where, "Assistants API call (shut down 2026-08-26)",
                            "Move to Responses + Conversations."))
    if re.search(r"chunk\.choices\[0\]", line) and not re.search(r"if\s+(not\s+)?chunk\.choices\b", context):
        out.append(Exposure("stream.unguarded_choices", "OA-17", "P3", None, where,
                            "chunk.choices[0] without a guard: IndexError on the include_usage chunk",
                            "Guard with `if chunk.choices:` before indexing."))
    if "connector_id" in line:
        out.append(Exposure("mcp.connector_id", "AG-12", "P3", None, where,
                            "connector_id is deprecated for OpenAI models released after 2026-09-01",
                            "Use server_url or tunnel_id."))
    return out


def _file_rules(text: str, rel: str, file_models: set[str]) -> list[Exposure]:
    out: list[Exposure] = []
    if "chat.completions.create" in text and "stream=True" in text and "include_usage" not in text:
        out.append(Exposure("stream.no_usage", "OA-17", "P3", None, rel,
                            "streamed Chat Completions without stream_options.include_usage: no token usage logged",
                            'Add stream_options={"include_usage": True}.'))
    calls_api = ("messages.create" in text) or ("chat.completions.create" in text)
    if calls_api and "max_retries" not in text and not re.search(
            r"enforced_spend_limit_reached|credit_balance_exhausted|spend_limit_exceeded|EnforcedSpendLimitReachedError", text):
        pack = "AN-01" if "messages.create" in text else "OA-04"
        out.append(Exposure("retry.sdk_default_on_billing_429", pack, "P3", None, rel,
                            "SDK default retries (2) with no billing-code check: non-retryable 429s are sent 3 times",
                            "Branch on error code (or EnforcedSpendLimitReachedError) and stop retrying billing failures."))
    return out


def _dossier_rules(d: dict[str, Any], today: date) -> list[Exposure]:
    out: list[Exposure] = []
    for dep in d.get("dependencies", []):
        name, ver = dep.get("name"), str(dep.get("version", ""))
        if name == "anthropic" and ver.split(".")[0].isdigit() and int(ver.split(".")[0]) < 2 and dep.get("upgrade_planned"):
            out.append(Exposure("sdk.major_upgrade", "AN-21", "P2", dep.get("upgrade_planned"), "dossier: dependencies",
                                f"anthropic {ver} -> 2.x planned {dep['upgrade_planned']}: API version becomes 2026-10-21",
                                "Remove sampling params and check prefill/thinking usage before the upgrade."))
    for plat in d.get("platforms", []):
        if plat in ("bedrock", "vertex") and any(m.startswith("claude-sonnet-4-5") for m in d.get("models_in_use", [])):
            out.append(Exposure("platform.schedule_differs", "AN-27", "P3", None, "dossier: platforms",
                                f"Sonnet 4.5 also used on {plat}: partner retirement dates differ from Anthropic's",
                                "Check the partner's own schedule; expect 'works on Bedrock, fails on API' reports after 2026-11-30."))
    return out


def _dedupe(fs: list[Exposure]) -> list[Exposure]:
    seen, out = set(), []
    for f in fs:
        key = (f.rule, f.where, f.evidence)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def draft_note(result: dict[str, Any], dossier: dict[str, Any]) -> str:
    """Markdown draft for the account team (CSM/AE/Applied AI). Never sent automatically."""
    fs = result["findings"]
    p1 = [f for f in fs if f["severity"] == "P1"]
    dated = sorted({f["deadline"] for f in fs if f["deadline"]})
    lines = [
        f"# Proactive exposure review — {dossier['account']}",
        "",
        f"*Draft for the account team · as of {result['as_of']} · {result['files_scanned']} files scanned · not sent*",
        "",
        "## Summary for the account thread",
        "",
        f"{len(fs)} items found, {len(p1)} urgent." + (f" Dated breaks: {', '.join(dated)}." if dated else ""),
        "",
        "## Customer-ready paragraph",
        "",
    ]
    if p1:
        # One sentence per urgent (rule, deadline) group: what breaks, when, where, what to change.
        groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for f in p1:
            groups.setdefault((f["rule"], f["deadline"]), []).append(f)
        sentences = []
        for (rule, deadline), items in groups.items():
            places = ", ".join(f"`{i['where'].split('/')[-1]}`" for i in items)
            fix = items[0]["fix"].split(". ")[0].rstrip(".")
            if rule == "model.retirement":
                model = items[0]["evidence"].split(":")[0]
                sentences.append(f"Your code references {model} in {len(items)} place(s) ({places}). That model "
                                 f"retires on {deadline}, and requests to it will fail from that date. "
                                 f"Recommended change: {fix}.")
            else:
                when = f"since {deadline}" if deadline else "now"
                sentences.append(f"{items[0]['evidence']} ({places}); this is failing {when}. Recommended change: {fix}.")
        lines.append("We reviewed the integration code you shared against current model and API changes. "
                     + " ".join(sentences) + " The other items below aren't urgent; we can go through them at the next sync.")
    else:
        lines.append("No urgent items. The hygiene items below reduce wasted retries and missing usage data.")
    lines += ["", "## Items", "", "| Sev | Deadline | Where | What | Fix | Pack |", "|---|---|---|---|---|---|"]
    for f in fs:
        lines.append(f"| {f['severity']} | {f['deadline'] or '—'} | `{f['where']}` | {f['evidence']} | {f['fix']} | {f['pack']} |")
    lines += ["", "## Contacts", ""]
    for role, who in (dossier.get("contacts") or {}).items():
        lines.append(f"- {role}: {who}")
    lines += ["", "*Generated by support-casebook from published deprecation/capability docs; verify dates on the "
              "deprecations pages before sending.*", ""]
    return "\n".join(lines)
