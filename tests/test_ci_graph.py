"""The static import graph used by test selection, checked against real imports.

This file imports every test module (by name, at run time), so it depends on all of them;
casebook.ci.select declares that in EXTRA_DEPS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from casebook.ci import select as S

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def graph() -> dict[str, set[str]]:
    return S.import_graph()


@pytest.mark.parametrize("test_file", sorted(p.name for p in (ROOT / "tests").glob("test_*.py")))
def test_sel_08_static_graph_covers_modules_loaded_at_import(graph, test_file: str) -> None:
    """Import the test module for real and compare sys.modules with what the AST analysis found."""
    mod = f"tests.{test_file[:-3]}"
    code = (f"import sys, importlib; importlib.import_module({mod!r}); "
            "print('\\n'.join(m for m in sys.modules if m.startswith('casebook')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    loaded = {m for m in out.stdout.split() if m in graph}
    deps = S.dependents(graph)
    static = {m for m in graph if mod in deps.get(m, set())}
    assert loaded - static - {"casebook"} == set(), f"{mod} loads modules the graph misses"


def test_sel_09_dynamic_dependencies_are_declared(graph) -> None:
    tests = {m for m in graph if m.startswith("tests.test_") and m != "tests.test_ci_graph"}
    assert tests <= graph["tests.test_ci_graph"]
    assert "casebook.rules" in graph["tests.test_ci"]          # via tests/ci_cases/case_assertion.py
