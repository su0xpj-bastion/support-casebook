"""Run the real Claude Code CLI (the copy bundled with claude-agent-sdk) against the casebook mock.

Isolation: every run gets a fresh, empty CLAUDE_CONFIG_DIR and working directory, so the user's own
~/.claude settings, credentials and history are never read or written. Nothing reaches Anthropic.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import claude_agent_sdk

CLAUDE = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
VERSION = (CLAUDE.parent / "claude.version").read_text().split()[0]
SCRUB = ("ANTHROPIC_", "CLAUDE_", "OTEL_", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY",
         "NODE_EXTRA_CA_CERTS", "AWS_", "GOOGLE_", "CLOUD_ML")


def run(base_url: str, *, env: dict[str, str] | None = None, args: list[str] | None = None,
        prompt: str = "Say ok.", project_settings: dict[str, Any] | None = None, user_settings: dict[str, Any] | None = None,
        files: dict[str, str] | None = None, timeout: float = 60) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as work:
        base = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB)}
        base.update({"ANTHROPIC_BASE_URL": base_url, "CLAUDE_CONFIG_DIR": cfg, "DISABLE_AUTOUPDATER": "1",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "HOME": cfg})
        for name, text in (files or {}).items():
            p = Path(work) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text.replace("{work}", work))
            if name.endswith(".sh"):
                p.chmod(0o755)
        if project_settings is not None:
            (Path(work) / ".claude").mkdir(exist_ok=True)
            (Path(work) / ".claude" / "settings.json").write_text(json.dumps(project_settings))
        if user_settings is not None:
            (Path(cfg) / "settings.json").write_text(json.dumps(user_settings).replace("{work}", work))
        merged = {**base, **{k: v.replace("{work}", work).replace("{cfg}", cfg) for k, v in (env or {}).items()}}
        cmd = [str(CLAUDE), "-p", prompt, "--output-format", "json", *(a.replace("{work}", work) for a in (args or []))]
        try:
            p = subprocess.run(cmd, cwd=work, env=merged, input="", capture_output=True, text=True, timeout=timeout)
            out = {"exit": p.returncode, "stdout": p.stdout[-3000:], "stderr": p.stderr[-3000:]}
        except subprocess.TimeoutExpired as e:
            out = {"exit": None, "timeout": True, "stdout": (e.stdout or b"")[-2000:] if isinstance(e.stdout, bytes) else str(e.stdout or "")[-2000:]}
    try:
        out["json"] = json.loads(out["stdout"]) if out.get("stdout", "").strip().startswith("{") else None
    except ValueError:
        out["json"] = None
    out["version"] = VERSION
    return out
