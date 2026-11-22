"""Failure signatures: classify a CI failure from its text, so the same failure is never
investigated from scratch twice.

Same model as the support packs: evidence (the failure text) -> verdict (class, retry, owner) -> fix.
Every signature here was matched against a failure actually produced on this machine
(tests/ci_cases/, exercised by tests/test_ci.py::test_signature_matches_reproduced_failure).

Retry semantics match casebook.rules: Y = rerun is the right first move (infra, flake),
N = rerunning can't help (the code or the environment definition is wrong), C = depends.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Signature:
    id: str
    cls: str          # infra | flake | dependency | regression | environment
    retry: str        # Y | N | C
    owner: str        # ci-team | author | dependency-owner
    pattern: str
    fix: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Order matters: specific before generic.
SIGNATURES: list[Signature] = [
    Signature("CI-01", "infra", "Y", "ci-team",
              r"Address already in use|EADDRINUSE|\[Errno (48|98)\]",
              "A fixed port collided with another process. Bind to port 0 and read the assigned port."),
    Signature("CI-02", "flake", "Y", "author",
              r"Directory not empty|ENOTEMPTY|\[Errno (66|39)\]",
              "Temp-dir cleanup raced a child process still writing. Wait for the child, or clean up with "
              "ignore_cleanup_errors=True (the OA-48 Codex test hit this once)."),
    Signature("CI-03", "infra", "Y", "ci-team",
              r"TimeoutExpired|timed out after \d+(\.\d+)? seconds",
              "A subprocess exceeded its timeout. Rerun once; if it repeats, the runner is overloaded or the "
              "process hangs (see AN-51 for a CLI that hangs instead of failing)."),
    Signature("CI-04", "environment", "Y", "ci-team",
              r"nodename nor servname provided|Name or service not known|Temporary failure in name resolution"
              r"|getaddrinfo (ENOTFOUND|EAI_AGAIN)",
              "DNS lookup failed. Tests must not depend on external hosts; if this one should, mark it and "
              "keep it out of the default suite."),
    Signature("CI-05", "dependency", "N", "author",
              r"Cannot find module|ERR_MODULE_NOT_FOUND",
              "Node dependency missing. Run `cd ts && npm ci`; if CI already does, add the package to "
              "ts/package.json and commit the lockfile."),
    Signature("CI-06", "dependency", "N", "author",
              r"ModuleNotFoundError: No module named|ImportError: cannot import name",
              "Python dependency or symbol missing. Pin it in requirements.txt, or fix the import."),
    Signature("CI-07", "regression", "N", "author",
              r"ERROR collecting|SyntaxError:|IndentationError:",
              "The test file doesn't import. Fix the error; nothing in that file ran."),
    Signature("CI-08", "regression", "N", "author",
              r"^diff --git a/docs/test-matrix\.md",
              "docs/test-matrix.md is out of date. Run `python tools/gen_test_matrix.py` and commit."),
    Signature("CI-09", "regression", "N", "author",
              r"links checked, [1-9]\d* problems",
              "A doc link is broken or a line link moved. Run `python tools/check_docs.py` and fix what it lists."),
    Signature("CI-10", "regression", "N", "author",
              r"AssertionError|^E\s+assert ",
              "A test assertion failed with the same result on rerun: treat as a real regression."),
]

UNKNOWN = Signature("CI-00", "unknown", "C", "ci-team", "",
                    "No signature matched. Rerun once to separate flake from regression, then add a signature.")


def classify(text: str) -> Signature:
    for s in SIGNATURES:
        if re.search(s.pattern, text, re.M):
            return s
    return UNKNOWN
