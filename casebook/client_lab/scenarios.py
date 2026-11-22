"""The real Claude Code and Codex CLIs against the casebook mock (AN-44..56, OA-43/48).

Claude Code: the copy bundled with claude-agent-sdk (2.1.329). Codex: @openai/codex 0.171.0 installed
under ts/. Every run uses a throwaway config home; nothing reads or writes ~/.claude or ~/.codex.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from casebook.client_lab import claude_cli as C
from casebook.client_lab import codex_cli as X
from casebook.client_lab.collector import collector
from casebook.client_lab.tls import make_chain
from casebook.mockserver import MockServer

KEY = "sk-ant-api03-envkeyAAAAAAAAAAAAAAAAAA"
ORG = "11111111-2222-3333-4444-555555555555"


def _msgs(srv: MockServer) -> list[dict[str, Any]]:
    return [q for q in srv.state.requests if "/messages" in q["path"]]


def _result(r: dict[str, Any]) -> str:
    return str((r.get("json") or {}).get("result") or r.get("stdout") or "")[:200]


def an44(srv: MockServer) -> dict[str, Any]:
    out = {}
    for label, env in (("api_key", {"ANTHROPIC_API_KEY": KEY}), ("auth_token", {"ANTHROPIC_AUTH_TOKEN": "gw-token-0001"}),
                       ("both", {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_AUTH_TOKEN": "gw-token-0001"})):
        srv.state.reset()
        r = C.run(srv.url, env=env)
        h = _msgs(srv)[-1]["headers"] if _msgs(srv) else {}
        out[label] = {"exit": r["exit"], "x-api-key_sent": bool(h.get("x-api-key")), "bearer_sent": bool(h.get("authorization"))}
    out["finding"] = {"cls": "client.credential_precedence", "detail": (
        f"ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN both set: x-api-key sent={out['both']['x-api-key_sent']}, "
        f"Authorization: Bearer sent={out['both']['bearer_sent']} — which one bills is decided by whatever receives it (API or gateway)")}
    return out


def an45(srv: MockServer) -> dict[str, Any]:
    out = {}
    for label, doc in (("forceLoginOrgUUID", {"forceLoginOrgUUID": ORG}), ("forceLoginMethod_claudeai", {"forceLoginMethod": "claudeai"})):
        srv.state.reset()
        r = C.run(srv.url, env={"ANTHROPIC_API_KEY": KEY}, args=["--managed-settings", json.dumps(doc)])
        out[label] = {"exit": r["exit"], "requests": len(_msgs(srv)),
                      "message": (r.get("stderr") or "").splitlines()[0][:220] if r.get("stderr") else None}
    out["finding"] = {"cls": "client.forced_login_blocks_env_key", "detail": (
        f"policy delivered with --managed-settings: forceLoginOrgUUID -> exit {out['forceLoginOrgUUID']['exit']}, "
        f"{out['forceLoginOrgUUID']['requests']} requests ('{(out['forceLoginOrgUUID']['message'] or '')[:90]}…'); "
        f"forceLoginMethod=claudeai alone -> exit {out['forceLoginMethod_claudeai']['exit']}")}
    return out


def an50(_: Any = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        ch = make_chain(Path(d))
        with MockServer(tls=(str(ch["cert"]), str(ch["key"]))) as srv:
            out = {}
            for label, extra in (("no_ca", {}), ("node_extra_ca_certs", {"NODE_EXTRA_CA_CERTS": str(ch["ca"])}),
                                 ("ca_path_wrong", {"NODE_EXTRA_CA_CERTS": "/nonexistent/corp-ca.pem"})):
                srv.state.reset()
                r = C.run(srv.url, env={"ANTHROPIC_API_KEY": KEY, **extra})
                out[label] = {"exit": r["exit"], "reached_api": len(srv.state.requests), "result": _result(r),
                              "stderr": (r.get("stderr") or "")[:200]}
    out["finding"] = {"cls": "client.tls_inspection", "detail": (
        f"untrusted chain -> '{out['no_ca']['result'][:95]}…'; NODE_EXTRA_CA_CERTS -> {out['node_extra_ca_certs']['result']}; "
        f"wrong CA path -> only a stderr warning, then the same TLS error")}
    return out


def an51(_: Any = None, wait: float = 20) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        ch = make_chain(Path(d))
        with MockServer(tls=(str(ch["cert"]), str(ch["key"]))) as srv:
            t = time.monotonic()
            r = C.run(srv.url, env={"ANTHROPIC_API_KEY": KEY, "NODE_EXTRA_CA_CERTS": str(ch["ca"]),
                                    "HTTPS_PROXY": "socks5://127.0.0.1:1"}, timeout=wait)
            elapsed = round(time.monotonic() - t, 1)
            reached = len(srv.state.requests)
    out = {"timed_out": bool(r.get("timeout")), "elapsed_s": elapsed, "reached_api": reached, "result": _result(r)}
    out["finding"] = {"cls": "client.socks_proxy", "detail": (
        f"HTTPS_PROXY=socks5://…: no error and no request after {elapsed}s (timed out={out['timed_out']}) — "
        "SOCKS is unsupported, and the symptom is a hang, not a message")}
    return out


def an55(srv: MockServer) -> dict[str, Any]:
    out = {}
    for label, script in (("ok", "#!/bin/sh\necho sk-ant-api03-helperCCCCCCCCCCCCCCCCCC\n"),
                          ("exit_1", "#!/bin/sh\necho 'vault: permission denied' >&2\nexit 1\n"),
                          ("empty", "#!/bin/sh\nexit 0\n")):
        srv.state.reset()
        r = C.run(srv.url, user_settings={"apiKeyHelper": "{work}/helper.sh"}, files={"helper.sh": script})
        h = _msgs(srv)[-1]["headers"] if _msgs(srv) else {}
        cred = h.get("x-api-key") or h.get("authorization") or ""
        out[label] = {"exit": r["exit"], "stderr": (r.get("stderr") or "").strip()[:160],
                      "credential_sent": "x-api-key" if h.get("x-api-key") else (cred.strip() if cred else None)}
    srv.state.reset()
    C.run(srv.url, env={"ANTHROPIC_API_KEY": KEY}, user_settings={"apiKeyHelper": "{work}/helper.sh"},
          files={"helper.sh": "#!/bin/sh\necho sk-ant-api03-helperCCCCCCCCCCCCCCCCCC\n"})
    h = _msgs(srv)[-1]["headers"]
    out["env_key_beats_helper"] = (h.get("x-api-key") or "").startswith("sk-ant-api03-envkey")
    out["finding"] = {"cls": "client.api_key_helper", "detail": (
        f"helper exits 1 -> stderr '{out['exit_1']['stderr'][:70]}', yet the request is still sent with "
        f"'{out['exit_1']['credential_sent']}' (empty credential); env key outranks the helper: {out['env_key_beats_helper']}")}
    return out


def an56(srv: MockServer) -> dict[str, Any]:
    out = {}
    for where in ("process_env", "user_settings", "project_settings"):
        with collector() as (url, seen):
            otel = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_METRICS_EXPORTER": "otlp", "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "OTEL_EXPORTER_OTLP_ENDPOINT": url,
                    "OTEL_METRIC_EXPORT_INTERVAL": "500", "OTEL_LOGS_EXPORT_INTERVAL": "500"}
            kw: dict[str, Any] = {"env": {"ANTHROPIC_API_KEY": KEY}}
            if where == "process_env":
                kw["env"] = {**kw["env"], **otel}
            elif where == "user_settings":
                kw["user_settings"] = {"env": otel}
            else:
                kw["project_settings"] = {"env": otel}
            C.run(srv.url, **kw)
            out[where] = sorted({x["path"] for x in seen})
    srv.state.reset()
    C.run(srv.url, env={"ANTHROPIC_API_KEY": KEY},
          project_settings={"env": {"ANTHROPIC_CUSTOM_HEADERS": "X-Casebook-Proof: project-settings-loaded"}})
    out["control_project_env_applied"] = any(q["headers"].get("x-casebook-proof") for q in srv.state.requests)
    out["finding"] = {"cls": "client.otel_project_settings", "detail": (
        f"OTLP exports: process env {out['process_env']}, user settings {out['user_settings']}, project settings "
        f"{out['project_settings']} (control: other project env vars applied={out['control_project_env_applied']})")}
    return out


def _codex_cfg(url: str) -> str:
    return (f'model = "gpt-6-luna"\nmodel_provider = "casebook"\n[model_providers.casebook]\nname = "casebook"\n'
            f'base_url = "{url}/v1"\nenv_key = "CASEBOOK_KEY"\nwire_api = "responses"\n'
            f'request_max_retries = 0\nstream_max_retries = 0\n')


def oa48(_: Any = None, wait: float = 20) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        ch = make_chain(Path(d))
        with MockServer(tls=(str(ch["cert"]), str(ch["key"]))) as srv:
            out = {"codex": X.version()}
            for label, extra in (("no_ca", {}), ("codex_ca_certificate", {"CODEX_CA_CERTIFICATE": str(ch["ca"])}),
                                 ("ssl_cert_file_fallback", {"SSL_CERT_FILE": str(ch["ca"])}),
                                 ("ca_path_wrong", {"CODEX_CA_CERTIFICATE": "/nonexistent/corp-ca.pem"})):
                srv.state.reset()
                r = X.run(["exec", "--skip-git-repo-check", "Say ok."], config=_codex_cfg(srv.url),
                          env={"CASEBOOK_KEY": "k", **extra}, stdin="", timeout=wait)
                msgs = [ln for ln in r["stderr"].splitlines() if ln.startswith(("ERROR", "error"))]
                out[label] = {"exit": r["exit"], "timed_out": bool(r.get("timeout")), "reached_api": len(srv.state.requests),
                              "answer": r["stdout"].strip()[:80], "errors": sorted(set(msgs))[:3]}
    out["finding"] = {"cls": "client.tls_inspection_codex", "detail": (
        f"untrusted chain -> {out['no_ca']['errors'] or 'no output'} until killed at {wait}s (retries=0); "
        f"CODEX_CA_CERTIFICATE -> '{out['codex_ca_certificate']['answer'][:30]}'; SSL_CERT_FILE fallback -> "
        f"'{out['ssl_cert_file_fallback']['answer'][:30]}'; wrong CA path -> same hang")}
    return out


def oa43(srv: MockServer) -> dict[str, Any]:
    login = X.run(["login", "--with-api-key"], stdin="sk-proj-casebookFAKE0000000000000000000000\n")
    out: dict[str, Any] = {"login_api_key": {"exit": login["exit"], "stderr": login["stderr"].strip()[-120:]}}
    for label, cfg in (("forced_chatgpt", 'forced_login_method = "chatgpt"\n'), ("forced_api", 'forced_login_method = "api"\n')):
        r = X.run(["login", "status"], config=cfg, home_files={"auth.json": json.dumps({"OPENAI_API_KEY": "sk-proj-casebookFAKE0000000000000000000000"})})
        out[label] = {"exit": r["exit"], "stdout": r["stdout"].strip()[:200], "stderr": r["stderr"].strip()[-240:],
                      "auth_json_after": "auth.json" in r["home_files_after"]}
    srv.state.reset()
    r = X.run(["exec", "--skip-git-repo-check", "Say ok."], stdin="", timeout=25,
              config=f'forced_login_method = "chatgpt"\nopenai_base_url = "{srv.url}/v1"\n',
              home_files={"auth.json": json.dumps({"OPENAI_API_KEY": "sk-proj-casebookFAKE0000000000000000000000"})})
    out["exec_forced_chatgpt"] = {"exit": r["exit"], "message": [ln for ln in r["stderr"].splitlines() if ln.strip()][-1:],
                                  "auth_json_after": "auth.json" in r["home_files_after"], "reached_api": len(srv.state.requests)}
    ex = out["exec_forced_chatgpt"]
    out["finding"] = {"cls": "client.forced_login_method", "detail": (
        f"API key stored + forced_login_method=chatgpt: `codex exec` -> exit {ex['exit']}, '{(ex['message'] or [''])[0]}', "
        f"auth.json deleted={not ex['auth_json_after']}; `codex login status` only says '{out['forced_chatgpt']['stderr']}'")}
    return out


SCENARIOS = {"AN-44": an44, "AN-45": an45, "AN-50": an50, "AN-51": an51, "AN-55": an55, "AN-56": an56,
             "OA-43": oa43, "OA-48": oa48}
