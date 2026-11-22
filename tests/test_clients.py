"""The real Claude Code (2.1.329) and Codex (0.171.0) CLIs against the mock, isolated config homes."""
from __future__ import annotations

import importlib.util

import pytest

from casebook.client_lab import codex_cli
from casebook.client_lab import scenarios as S

HAS_CLAUDE = importlib.util.find_spec("claude_agent_sdk") is not None
HAS_CODEX = codex_cli.CODEX.exists()
claude = pytest.mark.skipif(not HAS_CLAUDE, reason="claude-agent-sdk (bundled Claude Code) not installed")
codex = pytest.mark.skipif(not HAS_CODEX, reason="cd ts && npm ci")


@claude
def test_an44_both_credentials_sent(mock) -> None:
    r = S.an44(mock)
    assert r["api_key"]["x-api-key_sent"] and not r["api_key"]["bearer_sent"]
    assert r["auth_token"]["bearer_sent"] and not r["auth_token"]["x-api-key_sent"]
    assert r["both"]["x-api-key_sent"] and r["both"]["bearer_sent"]


@claude
def test_an45_forced_org_blocks_env_key(mock) -> None:
    r = S.an45(mock)
    assert r["forceLoginOrgUUID"]["exit"] == 1 and r["forceLoginOrgUUID"]["requests"] == 0
    assert r["forceLoginOrgUUID"]["message"].startswith("This machine's managed settings require a first-party login")
    assert r["forceLoginMethod_claudeai"]["exit"] == 0


@claude
def test_an50_tls_inspection() -> None:
    r = S.an50()
    assert r["no_ca"]["exit"] == 1 and "SELF_SIGNED_CERT_IN_CHAIN" in r["no_ca"]["result"] and r["no_ca"]["reached_api"] == 0
    assert r["node_extra_ca_certs"]["result"] == "ok"
    assert "Ignoring extra certs" in r["ca_path_wrong"]["stderr"] and r["ca_path_wrong"]["exit"] == 1


@claude
def test_an51_socks_proxy_hangs() -> None:
    r = S.an51(wait=12)
    assert r["timed_out"] and r["reached_api"] == 0


@claude
def test_an55_api_key_helper(mock) -> None:
    r = S.an55(mock)
    assert r["ok"]["credential_sent"] == "x-api-key"
    assert r["exit_1"]["stderr"].startswith("apiKeyHelper failed: exited 1") and r["exit_1"]["credential_sent"] == "Bearer"
    assert r["empty"]["stderr"].startswith("apiKeyHelper failed: did not return a value")
    assert r["env_key_beats_helper"]


@claude
def test_an56_otel_ignored_in_project_settings(mock) -> None:
    r = S.an56(mock)
    assert r["process_env"] and r["user_settings"] and r["project_settings"] == []
    assert r["control_project_env_applied"]


@codex
def test_oa43_forced_login_method(mock) -> None:
    r = S.oa43(mock)
    ex = r["exec_forced_chatgpt"]
    assert ex["exit"] == 1 and ex["message"] == ["ChatGPT login is required, but an API key is currently being used. Logging out."]
    assert ex["auth_json_after"] is False and ex["reached_api"] == 0
    assert r["forced_chatgpt"]["stderr"] == "Not logged in" and r["forced_api"]["exit"] == 0


@codex
def test_oa48_tls_inspection_codex() -> None:
    r = S.oa48(wait=12)
    assert r["no_ca"]["timed_out"] and "ERROR: Reconnecting... waiting for network" in r["no_ca"]["errors"]
    assert r["codex_ca_certificate"]["answer"].startswith("Claim #4411")
    assert r["ssl_cert_file_fallback"]["answer"].startswith("Claim #4411")
    assert r["ca_path_wrong"]["timed_out"]
