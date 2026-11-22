"""Run the real Codex CLI (project-local npm install, ts/node_modules) against the casebook mock.

Isolation: a fresh CODEX_HOME per run, so the user's ~/.codex auth and config are never read or
written; OPENAI_*/CODEX_* variables from the parent shell are scrubbed. Nothing reaches OpenAI.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TS = Path(__file__).resolve().parents[2] / "ts"
CODEX = TS / "node_modules" / ".bin" / "codex"
SCRUB = ("OPENAI_", "CODEX_", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY")


def version() -> str:
    return subprocess.run([str(CODEX), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()


def run(args: list[str], *, config: str = "", env: dict[str, str] | None = None, stdin: str | None = None,
        home_files: dict[str, str] | None = None, timeout: float = 60) -> dict[str, Any]:
    # ignore_cleanup_errors: a Codex killed at the timeout can leave child processes writing to its home
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        (Path(home) / "config.toml").write_text(config)
        for name, text in (home_files or {}).items():
            (Path(home) / name).write_text(text)
        base = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB)}
        merged = {**base, "CODEX_HOME": home, "NO_COLOR": "1", **(env or {})}
        try:
            p = subprocess.run([str(CODEX), *args], cwd=work, env=merged, input=stdin, capture_output=True,
                               text=True, timeout=timeout)
            out: dict[str, Any] = {"exit": p.returncode, "stdout": p.stdout[-3000:], "stderr": p.stderr[-3000:]}
        except subprocess.TimeoutExpired as e:
            def _s(b: Any) -> str:
                return (b.decode(errors="replace") if isinstance(b, bytes) else (b or ""))[-3000:]
            out = {"exit": None, "timeout": True, "stdout": _s(e.stdout), "stderr": _s(e.stderr)}
        out["home_files_after"] = sorted(x.name for x in Path(home).iterdir())
        out["log_tail"] = _sqlite_logs(Path(home))
    return out


def _sqlite_logs(home: Path, limit: int = 400) -> str:
    """Codex 0.171.0 writes its logs to CODEX_HOME/logs_2.sqlite, not to text files."""
    import sqlite3

    db = home / "logs_2.sqlite"
    if not db.exists():
        return ""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        lines: list[str] = []
        for (table,) in con.execute("select name from sqlite_master where type='table'"):
            cols = [c[1] for c in con.execute(f"pragma table_info({table})")]
            text_cols = [c for c in cols if c in ("level", "target", "feedback_log_body", "message")]
            if not text_cols:
                continue
            for row in con.execute(f"select {', '.join(text_cols)} from {table} limit {limit}"):
                lines.append(" | ".join(str(v) for v in row if v is not None))
        return "\n".join(lines)[-6000:]
    finally:
        con.close()
