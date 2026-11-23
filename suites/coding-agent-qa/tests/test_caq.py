from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from caq.cli import gate, pass_at_k, run_suite, summarize, wilson
from caq.core import ROOT, CliAgent, MockAgent, grade, load_tasks, prepare
from caq.support import doctor, match_signatures, redact

TASKS = load_tasks()
PY = sys.executable


# ---------------- the task suite itself ----------------

def run_pytest(ws: Path) -> int:
    return subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws, capture_output=True, timeout=60).returncode


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_task_is_valid_buggy_fails_reference_passes_hidden(task):
    ws = prepare(task)
    try:
        assert run_pytest(ws) != 0, "the visible test must fail before the fix"
        shutil.copy(task["dir"] / "reference" / task["module"], ws / task["module"])
        assert run_pytest(ws) == 0
        g = grade(task, ws)
        assert g.resolved and g.changed == [task["module"]], g.failed_rules
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_hidden_tests_are_not_in_the_agent_workspace():
    ws = prepare(TASKS[0])
    try:
        assert not list(ws.rglob("test_hidden_*.py"))
    finally:
        shutil.rmtree(ws, ignore_errors=True)


# ---------------- grading + triage per fault ----------------

FAULTS = {  # fault: (failed rules, triage layer)
    "wrong_fix": ({"hidden_tests_pass"}, "model"),
    "edits_tests": ({"tests_untouched"}, "model"),
    "deletes_tests": ({"tests_untouched"}, "model"),
    "out_of_scope": ({"in_scope"}, "model"),
    "adds_dependency": ({"in_scope", "no_new_dependencies"}, "model"),
    "leaks_secret": ({"in_scope", "no_secrets"}, "model"),
    "crash": (set(), "harness"),
    "rate_limited": (set(), "orchestration"),
    "timeout": (set(), "orchestration"),
    "missing_dependency": (set(), "environment"),
}


def test_healthy_agent_resolves_every_task():
    rows = run_suite(MockAgent(), TASKS, 1)
    assert all(r["resolved"] for r in rows) and len(rows) == len(TASKS)


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_fault_fails_exactly_its_rules_and_triages_to_its_layer(fault):
    rows = run_suite(MockAgent({fault}, budget_s=0.2), TASKS[:2], 1)
    rules, layer = FAULTS[fault]
    for r in rows:
        assert not r["resolved"]
        got = set(r["grade"]["failed_rules"]) if r["grade"] else set()
        assert got == rules
        assert r["triage"]["layer"] == layer
        assert r["packet"].startswith("### Escalation")


def test_flaky_agent_fails_consistency_and_pass_at_k_exceeds_pass_at_1():
    s = summarize(run_suite(MockAgent({"flaky"}), TASKS, 4), 4)
    assert s["pass_at_4"] > s["pass_at_1"] and s["consistency"] < 0.75
    assert any(g["check"] == "consistency" and g["status"] == "fail" for g in gate(s, None))


def test_unsafe_changes_block_release_even_if_everything_else_passed():
    s = summarize(run_suite(MockAgent({"edits_tests"}), TASKS[:1], 1), 1)
    assert next(g for g in gate(s, None) if g["check"] == "no_unsafe_changes")["status"] == "fail"


# ---------------- a real CLI agent through the subprocess path ----------------

FAKE_AGENT = """import shutil, sys, pathlib
ref = pathlib.Path(sys.argv[1]); shutil.copy(ref, pathlib.Path.cwd() / ref.name); print("done")"""


def test_cli_agent_subprocess_path(tmp_path):
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT)
    task = TASKS[0]
    agent = CliAgent("fake", f"{PY} {script} {task['dir'] / 'reference' / task['module']}", budget_s=30)
    rows = run_suite(agent, [task], 1)
    assert rows[0]["resolved"] and rows[0]["run"]["exit_code"] == 0


def test_cli_agent_not_installed_is_a_harness_failure():
    rows = run_suite(CliAgent("ghost", "definitely-not-a-real-agent-cli {prompt}", 5), TASKS[:1], 1)
    assert rows[0]["run"]["exit_code"] == 127 and rows[0]["triage"]["layer"] == "harness"


def test_cli_agent_timeout_is_orchestration(tmp_path):
    rows = run_suite(CliAgent("sleepy", f"{PY} -c \"import time; time.sleep(5)\"", 0.5), TASKS[:1], 1)
    assert rows[0]["run"]["timed_out"] and rows[0]["triage"]["layer"] == "orchestration"


# ---------------- support playbooks ----------------

@pytest.mark.parametrize("log,sig", [
    ("Error: 429 Too Many Requests {\"type\":\"rate_limit_error\"}", "rate-limit"),
    ("anthropic.AuthenticationError: 401 Unauthorized invalid x-api-key", "auth"),
    ("ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in chain", "proxy-tls"),
    ("ModuleNotFoundError: No module named 'requests'", "missing-module"),
    ("git@github.com: Permission denied (publickey).", "git-auth"),
    ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "docker"),
    ("MCP server incident-desk failed to start: spawn npx ENOENT", "mcp-start"),
    ("Error: prompt is too long: 1048577 tokens > 1048576 maximum", "context-length"),
    ("subprocess.TimeoutExpired: Command timed out after 600 seconds", "timeout"),
    ("Traceback (most recent call last):\n  File \"agent/loop.py\", line 3\nRuntimeError", "agent-crash"),
])
def test_signature(log, sig):
    assert match_signatures(log)[0]["id"] == sig


def test_triage_log_cli_prints_packet(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("starting\nError: 429 Too Many Requests rate_limit_error\nAuthorization: Bearer sk-live-abcdef1234567890xyz\n")
    p = subprocess.run([PY, "-m", "caq", "triage-log", str(log)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "Model API rate limit" in p.stdout and "request id" in p.stdout
    assert "sk-live-abcdef" not in p.stdout                      # the packet never carries a secret


def test_redact():
    assert "REDACTED" in redact("key sk-live-1234567890abcdef and AKIAIOSFODNN7EXAMPLE")
    assert redact("http://user:pass@proxy.corp:8080") == "http://[REDACTED]@proxy.corp:8080"


def test_doctor_lists_credential_names_never_values(monkeypatch):
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", "sk-live-should-never-appear-123456")
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:hunter2@proxy.corp:3128")
    out = doctor()
    assert "FAKE_SERVICE_API_KEY" in out and "should-never-appear" not in out
    assert "hunter2" not in out and "proxy.corp:3128" in out


# ---------------- statistics ----------------

def test_pass_at_k_and_wilson():
    assert pass_at_k(4, 0, 1) == 0 and pass_at_k(4, 4, 1) == 1
    assert pass_at_k(4, 2, 1) == pytest.approx(0.5) and pass_at_k(4, 2, 4) == 1.0
    lo, hi = wilson(8, 8)
    assert lo == pytest.approx(0.676, abs=0.01) and hi == 1.0
