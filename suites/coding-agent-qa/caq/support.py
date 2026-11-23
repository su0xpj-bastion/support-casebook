"""Support side: triage a failed run by layer, match logs to playbooks, collect a repro bundle.

Layers:
  harness        the agent itself crashed or couldn't start (a product bug)
  orchestration  the loop around the model failed: rate limits, timeouts, MCP, context length
  environment    the customer's setup: credentials, proxy/TLS, missing modules, git, docker
  model          the run completed, but the change is wrong, out of scope, or games the tests
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .core import ROOT, AgentRun, Grade

PLAYBOOK = json.loads((ROOT / "playbooks" / "signatures.json").read_text())["signatures"]
SECRETY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.I)


def match_signatures(log: str) -> list[dict]:
    return [s for s in PLAYBOOK if re.search(s["pattern"], log or "", re.I)]


def triage(run: AgentRun, g: Grade | None) -> dict:
    """Return {"layer", "reason", "signature"} for a run that did not resolve the task."""
    if run.timed_out:
        return {"layer": "orchestration", "reason": "ran past its time budget", "signature": "timeout"}
    hits = match_signatures(run.log)
    if run.exit_code != 0 and hits:
        s = hits[0]
        return {"layer": s["layer"], "reason": s["title"], "signature": s["id"]}
    if run.exit_code == 127:
        return {"layer": "harness", "reason": "agent command not found", "signature": None}
    if run.exit_code != 0:
        return {"layer": "harness", "reason": f"agent exited {run.exit_code} with no known signature", "signature": None}
    if g is None:
        return {"layer": "harness", "reason": "no grade", "signature": None}
    if not g.tests_untouched:
        return {"layer": "model", "reason": "modified or deleted tests instead of fixing the code", "signature": None}
    if not g.no_secrets:
        return {"layer": "model", "reason": "wrote a credential into the repository", "signature": None}
    if not g.in_scope or not g.no_new_deps:
        return {"layer": "model", "reason": f"changed files outside the fix: {', '.join(c for c in g.changed)}", "signature": None}
    return {"layer": "model", "reason": "completed, but the hidden tests fail: the fix is wrong or incomplete", "signature": None}


def escalation_packet(task_id: str, run: AgentRun, g: Grade | None, t: dict) -> str:
    sig = next((s for s in PLAYBOOK if s["id"] == t["signature"]), None)
    lines = [f"### Escalation: {task_id}", "", f"**Layer:** {t['layer']}  ", f"**Finding:** {t['reason']}  ",
             f"**Agent exit:** {run.exit_code}{' (timed out)' if run.timed_out else ''}, {run.seconds:.1f} s"]
    if g:
        lines.append(f"**Grade:** failed rules {g.failed_rules or 'none'}; changed {g.changed or 'nothing'}")
    if sig:
        lines += ["", "**First checks:**"] + [f"1. {s}" for s in sig["steps"]]
        lines += ["", "**Include when escalating:** " + "; ".join(sig["escalate_with"])]
    tail = "\n".join((run.log or "").strip().splitlines()[-12:])
    lines += ["", "**Log tail:**", "```", redact(tail), "```", ""]
    return "\n".join(lines)


def redact(text: str) -> str:
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "sk-[REDACTED]", text)
    text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "AKIA[REDACTED]", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]{12,}", r"\1[REDACTED]", text)
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)


def _version(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return "not installed"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (p.stdout or p.stderr).strip().splitlines()[0][:80] if (p.stdout or p.stderr).strip() else "?"
    except (OSError, subprocess.SubprocessError):
        return "error running --version"


def doctor() -> str:
    """Environment snapshot for a support ticket. Never prints secret values."""
    tools = {"python": ["python3", "--version"], "git": ["git", "--version"], "node": ["node", "--version"],
             "docker": ["docker", "--version"], "claude": ["claude", "--version"], "codex": ["codex", "--version"],
             "muse": ["muse", "--version"], "gh": ["gh", "--version"]}
    lines = ["## Environment snapshot", "", f"- OS: {platform.system()} {platform.release()} ({platform.machine()})"]
    lines += [f"- {name}: {_version(cmd)}" for name, cmd in tools.items()]
    keys = sorted(k for k in os.environ if SECRETY.search(k) and not k.startswith("_"))
    lines += ["", "**Credential env vars present (values never shown):** " + (", ".join(keys) or "none")]
    proxies = {k: os.environ[k] for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy") if k in os.environ}
    lines += ["**Proxy settings:** " + (", ".join(f"{k}={redact(v)}" for k, v in proxies.items()) or "none")]
    ca = [k for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS") if k in os.environ]
    lines += ["**Custom CA bundle:** " + (", ".join(ca) or "none")]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5).stdout.strip()
        lines += [f"**git user configured:** {'yes' if name else 'no'}"]
    except (OSError, subprocess.SubprocessError):
        pass
    return "\n".join(lines) + "\n"
