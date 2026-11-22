"""Change-based test selection.

Given the files changed between two commits, pick the tests that can observe the change:

1. Python modules: build the import graph of casebook/ and tests/ from the AST (every import,
   including ones inside functions). A changed module selects every test file that imports it,
   directly or through other modules. tests/conftest.py counts as imported by the test files that use
   its `mock` fixture.
2. Data files a module reads at runtime (ts/, the packet schema, dossiers, ...) count as a change
   to that module. The table is DATA_DEPS; it was built by searching the source for Path(__file__).
3. A changed pack (faults/<provider>/<ID>-*/) selects only the tests that name that pack,
   plus the test files that load every pack.
4. Files that can't affect tests (docs, generated runbooks, reports) select nothing.
5. Anything not covered above selects the whole suite. Unknown means run everything.
"""
from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Runtime data -> the module that reads it (a change to the data is a change to that module).
DATA_DEPS: list[tuple[str, str]] = [
    ("ts/*", "casebook.ts_repro"),
    ("ts/*", "casebook.client_lab.codex_cli"),
    ("ts/*", "casebook.mcp_lab.servers"),
    ("casebook/schema/*", "casebook.packet"),
    ("cases/dossiers/*", "casebook.exposure"),
    ("cases/samples/*", "casebook.exposure"),
    ("triage/adversarial.yaml", "casebook.triage.dataset"),
]
# Data read directly by a test file.
DATA_TESTS: list[tuple[str, str]] = [
    ("tests/ci_cases/*", "tests/test_ci.py"),
    ("triage/eval/*", "tests/test_triage.py"),
    ("cases/dossiers/*", "tests/test_exposure.py"),
]
# Files that no test reads.
NO_TESTS = ["docs/*", "README.md", "runbooks/*", "real/*", "cases/sims/*", "cases/examples/*",
            "cases/README.md", "tools/*", ".gitignore", "ci/*"]
# Files that can change any test's behaviour.
RUN_ALL = ["requirements.txt", "pyproject.toml", "tests/conftest.py", ".github/*", "casebook/ci/pytest_plugin.py"]
# Test files that iterate over every pack.
ALL_PACK_TESTS = ["tests/test_triage.py"]

PACK_DIR = re.compile(r"^faults/[^/]+/([A-Z]+-\d+)-[^/]+/")


@dataclass
class Selection:
    run_all: bool = False
    files: set[str] = field(default_factory=set)        # whole test files
    nodes: set[str] = field(default_factory=set)        # individual test node ids
    reasons: dict[str, list[str]] = field(default_factory=dict)

    def why(self, target: str, reason: str) -> None:
        self.reasons.setdefault(target, []).append(reason)

    def pytest_args(self) -> list[str]:
        """Arguments for pytest. Empty list with run_all=False means nothing to run."""
        if self.run_all:
            return ["tests"]
        nodes = sorted(n for n in self.nodes if n.split("::")[0] not in self.files)
        return sorted(self.files) + nodes


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    """Every casebook/tests module this file imports, anywhere in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    me = _module_name(path)
    pkg = me if path.name == "__init__.py" else me.rpartition(".")[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[: len(base) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)   # `from casebook import packs` imports a module
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__" \
                and node.args and isinstance(node.args[0], ast.Constant):
            out.add(str(node.args[0].value))
    return {m for m in out if m.split(".")[0] in ("casebook", "tests")}


def import_graph() -> dict[str, set[str]]:
    """module -> modules it imports (only modules that exist in this repo)."""
    files = [p for p in (ROOT / "casebook").rglob("*.py")] + [p for p in (ROOT / "tests").glob("*.py")]
    names = {_module_name(p): p for p in files}
    graph: dict[str, set[str]] = {}
    for name, path in names.items():
        deps = set()
        for m in _imports(path):
            # `casebook.packs.get` -> casebook.packs; keep the longest prefix that is a real module
            parts = m.split(".")
            for i in range(len(parts), 0, -1):
                cand = ".".join(parts[:i])
                if cand in names:
                    deps.add(cand)
                    # importing casebook.a.b also runs casebook/__init__.py and casebook/a/__init__.py
                    deps.update(".".join(parts[:j]) for j in range(1, i) if ".".join(parts[:j]) in names)
                    break
        if name.startswith("tests.test_") and _uses_fixture(path, CONFTEST_FIXTURES):
            deps.add("tests.conftest")
        graph[name] = {d for d in deps if d != name}
    # Dependencies the AST can't see (found by the mutation experiment, see docs/ci.md):
    # tests/test_ci.py runs tests/ci_cases/*.py in subprocesses, so it depends on what they import;
    # tests/test_ci_graph.py imports every test module by name.
    for case in (ROOT / "tests" / "ci_cases").glob("*.py"):
        try:
            mods = _imports(case)
        except SyntaxError:      # case_syntax.py is broken on purpose
            continue
        graph.setdefault("tests.test_ci", set()).update(_resolve(mods, names))
    graph["tests.test_ci_graph"] = graph.get("tests.test_ci_graph", set()) | {
        m for m in names if m.startswith("tests.test_") and m != "tests.test_ci_graph"}
    return graph


def _resolve(mods: set[str], names: dict[str, Path]) -> set[str]:
    out = set()
    for m in mods:
        parts = m.split(".")
        for i in range(len(parts), 0, -1):
            if ".".join(parts[:i]) in names:
                out.add(".".join(parts[:i]))
                break
    return out


# Fixtures defined in tests/conftest.py. A test file depends on conftest's imports only if it uses one.
CONFTEST_FIXTURES = {"mock"}


def _uses_fixture(path: Path, fixtures: set[str]) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(a.arg in fixtures for a in node.args.args):
                return True
    return False


def dependents(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    """module -> every module that imports it directly or transitively."""
    rev: dict[str, set[str]] = {m: set() for m in graph}
    for m, deps in graph.items():
        for d in deps:
            rev.setdefault(d, set()).add(m)
    out: dict[str, set[str]] = {}
    for m in rev:
        seen: set[str] = set()
        stack = [m]
        while stack:
            for nxt in rev.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[m] = seen
    return out


def _test_files_for_module(mod: str, deps: dict[str, set[str]]) -> set[str]:
    users = deps.get(mod, set()) | {mod}
    return {f"tests/{u.split('.', 1)[1]}.py" for u in users if u.startswith("tests.test_")}


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def pack_nodes(pack_id: str, collected: list[str]) -> set[str]:
    short = pack_id.replace("-", "").lower()
    out = set()
    for nid in collected:
        name = nid.split("::", 1)[1] if "::" in nid else nid
        base = name.split("[")[0]
        if f"[{pack_id}]" in name or name.startswith(f"{base}[{pack_id}-") or re.search(rf"_{short}(_|$)", base):
            out.add(nid)
    return out


def collect() -> list[str]:
    import sys
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                       capture_output=True, text=True, check=True)
    return [line for line in r.stdout.splitlines() if "::" in line]


def select(changed: list[str], *, collected: list[str] | None = None,
           graph: dict[str, set[str]] | None = None) -> Selection:
    graph = graph if graph is not None else import_graph()
    deps = dependents(graph)
    sel = Selection()

    def add_module(mod: str, because: str) -> None:
        for f in _test_files_for_module(mod, deps):
            sel.files.add(f)
            sel.why(f, because)

    for path in changed:
        if any(fnmatch.fnmatch(path, g) for g in RUN_ALL):
            sel.run_all = True
            sel.why("*", f"{path}: can affect every test")
            continue
        m = PACK_DIR.match(path)
        if m:
            pid = m.group(1)
            collected = collected if collected is not None else collect()
            for nid in pack_nodes(pid, collected):
                sel.nodes.add(nid)
                sel.why(nid, f"{path}: pack {pid}")
            for f in ALL_PACK_TESTS:
                sel.files.add(f)
                sel.why(f, f"{path}: this test reads every pack")
            continue
        if path.endswith(".py") and (path.startswith("casebook/") or path.startswith("tests/")):
            p = ROOT / path
            if not p.exists():        # deleted module: whoever imported it must be re-checked
                sel.run_all = True
                sel.why("*", f"{path}: deleted")
                continue
            mod = _module_name(p)
            if mod.startswith("tests.test_"):
                sel.files.add(path)
                sel.why(path, f"{path}: test file changed")
            else:
                add_module(mod, f"{path} changed")
            continue
        hit = False
        for glob, mod in DATA_DEPS:
            if fnmatch.fnmatch(path, glob):
                add_module(mod, f"{path}: data read by {mod}")
                hit = True
        for glob, test in DATA_TESTS:
            if fnmatch.fnmatch(path, glob):
                sel.files.add(test)
                sel.why(test, f"{path}: data read by {test}")
                hit = True
        if hit:
            continue
        if any(fnmatch.fnmatch(path, g) for g in NO_TESTS):
            continue
        sel.run_all = True
        sel.why("*", f"{path}: no known mapping, so run everything")
    return sel
