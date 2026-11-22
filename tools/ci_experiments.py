"""Experiments behind the CI numbers in docs/ci.md. Each writes JSON to ci/reports/.

    python tools/ci_experiments.py replay        # selection over every commit in this repo's history
    python tools/ci_experiments.py mutate        # inject one fault per module; does selection still catch it?
    python tools/ci_experiments.py flakes -n 5   # run the full suite n times on one commit, find flakes
    python tools/ci_experiments.py queue         # merge queue: batch sizes vs test runs on a generated repo
    python tools/ci_experiments.py actions       # shard timings from GitHub Actions (needs gh)

mutate and flakes take a long time (each runs the whole suite several times).
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from casebook.ci import mergequeue as MQ  # noqa: E402
from casebook.ci import quarantine as Q  # noqa: E402
from casebook.ci import select as S  # noqa: E402
from casebook.ci import shard as SH  # noqa: E402

OUT = ROOT / "ci" / "reports"


def _save(name: str, data: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"-> ci/reports/{name}.json")


def _git(*a: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- replay

def replay() -> None:
    """For each commit: what would selection have run? Seconds are estimated from ci/durations.json
    (a selected file counts in full even if only some of its tests were selected: an upper bound)."""
    durations = SH.load_durations()
    full = sum(durations.values())
    graph = S.import_graph()
    collected = S.collect()
    shas = _git("rev-list", "--reverse", "--first-parent", "HEAD").split()
    rows = []
    for parent, sha in zip(shas, shas[1:]):
        changed = _git("diff", "--name-only", parent, sha).split()
        sel = S.select(changed, collected=collected, graph=graph)
        files = set(SH.test_files()) if sel.run_all else sel.files | {n.split("::")[0] for n in sel.nodes}
        est = sum(durations.get(f, 0.0) for f in files)
        rows.append({"sha": sha[:8], "changed": len(changed), "run_all": sel.run_all, "test_files": len(files),
                     "nodes": len(sel.nodes), "est_seconds": round(est, 1),
                     "why_all": sel.reasons.get("*", [])[:2]})
    _save("selection-replay", {"full_suite_seconds": round(full, 1), "commits": rows})
    for r in rows:
        print(f"{r['sha']} {'ALL ' if r['run_all'] else '    '} {r['test_files']:2} files {r['est_seconds']:6.1f}s")


# ---------------------------------------------------------------- mutation

SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
        ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def _mutate(path: Path, nth: int = 0) -> str | None:
    """Flip the nth comparison operator inside a function. Returns a description, or None."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and type(node.ops[0]) in SWAP:
                if seen == nth:
                    old = type(node.ops[0]).__name__
                    node.ops[0] = SWAP[type(node.ops[0])]()
                    path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
                    return f"{fn.name}:{node.lineno} {old} -> {type(node.ops[0]).__name__}"
                seen += 1
    return None


def _run_tests(cwd: Path, args: list[str]) -> dict[str, object]:
    if not args:
        return {"ran": False}
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *args], cwd=cwd,
                       capture_output=True, text=True)
    return {"ran": True, "failed": p.returncode != 0, "seconds": round(time.monotonic() - t0, 1),
            "tail": p.stdout.strip().splitlines()[-1:] if p.stdout else []}


def mutate(modules: list[str]) -> None:
    """In a throwaway worktree: mutate one module, run the selected tests, then everything else.
    A mutant caught only by unselected tests is a selection miss."""
    graph = S.import_graph()
    wt = Path(tempfile.mkdtemp(prefix="cb-mut-"))
    shutil.rmtree(wt)
    _git("worktree", "add", "--detach", str(wt), "HEAD")
    (wt / "ts" / "node_modules").symlink_to(ROOT / "ts" / "node_modules")
    results = []
    try:
        for mod in modules:
            path = wt / mod
            original = path.read_text(encoding="utf-8")
            desc = _mutate(path)
            if desc is None:
                results.append({"module": mod, "skipped": "no comparison to flip"})
                continue
            sel = S.select([mod], graph=graph)
            sel_files = sorted(sel.files)
            rest = [f for f in SH.test_files() if f not in sel_files]
            caught = _run_tests(wt, sel_files)
            others = _run_tests(wt, rest)
            path.write_text(original, encoding="utf-8")
            miss = bool(others.get("failed")) and not caught.get("failed")
            results.append({"module": mod, "mutation": desc, "selected_files": sel_files,
                            "selected": caught, "unselected": others, "selection_miss": miss})
            print(f"{mod:40} {desc:40} selected={'caught' if caught.get('failed') else 'missed'} "
                  f"unselected={'failed' if others.get('failed') else 'passed'}{'  MISS' if miss else ''}")
    finally:
        _git("worktree", "remove", "--force", str(wt))
    _save("selection-mutation", results)


# ---------------------------------------------------------------- flakes

def flakes(n: int) -> None:
    hist = ROOT / ".ci" / "flake-history.jsonl"
    runs = []
    for i in range(n):
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-m", "casebook", "ci", "test", "--history", str(hist),
                            "--report", str(ROOT / ".ci" / f"flake-run-{i}.json")], cwd=ROOT,
                           capture_output=True, text=True)
        rep = json.loads((ROOT / ".ci" / f"flake-run-{i}.json").read_text())
        runs.append({"run": i, "exit": p.returncode, "seconds": round(time.monotonic() - t0, 1),
                     "failures": rep.get("failures", [])})
        print(f"run {i}: exit {p.returncode}, {len(rep.get('failures', []))} failure(s)")
    rows = Q.load_history(hist)
    stats = Q.analyse(rows)
    flaky = {k: v for k, v in stats.items() if v["flaky_commits"]}
    durations = SH.file_durations(rows)
    SH.save_durations(durations)
    _save("flakes", {"runs": runs, "tests": len(stats), "test_runs": len(rows),
                     "flaky": flaky, "consistent_failures": {k: v for k, v in stats.items()
                                                             if v["consistent_fail_commits"]}})


# ---------------------------------------------------------------- merge queue

def queue() -> None:
    """16 branches, 2 of them break the tests, 1 conflicts. Same queue at batch sizes 1, 2, 4, 8,
    plus the same 13 good branches with no failures."""
    test_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    out = {}
    for scenario, bad in (("2 failing + 1 conflict", True), ("no failures", False)):
        for batch in (1, 2, 4, 8):
            tmp = Path(tempfile.mkdtemp(prefix="cb-mq-"))
            repo, branches = _queue_repo(tmp, bad)
            t0 = time.monotonic()
            res = MQ.MergeQueue(repo, "main", test_cmd, batch_size=batch).process(branches)
            final = subprocess.run(test_cmd, cwd=repo, capture_output=True, text=True).returncode == 0
            out.setdefault(scenario, []).append({
                "batch": batch, "test_runs": res.test_runs, "test_seconds": round(res.test_seconds, 1),
                "wall_seconds": round(time.monotonic() - t0, 1), "landed": len(res.landed),
                "ejected": res.ejected, "main_green_after": final})
            shutil.rmtree(tmp, ignore_errors=True)
            r = out[scenario][-1]
            print(f"{scenario:24} batch {batch}: {r['test_runs']:2} runs, landed {r['landed']}, ejected {sorted(r['ejected'])}")
    _save("merge-queue", out)


def _queue_repo(tmp: Path, with_bad: bool) -> tuple[Path, list[str]]:
    r = tmp / "repo"
    r.mkdir()
    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=r, check=True, capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "ci")
    (r / "core.py").write_text("def total(xs):\n    return sum(xs)\n")
    (r / "test_core.py").write_text("import time\nfrom core import total\n\n"
                                    "def test_total():\n    time.sleep(0.5)  # a suite that takes a moment\n"
                                    "    assert total([1, 2, 3]) == 6\n")
    git("add", "."), git("commit", "-qm", "base")
    branches = []
    for i in range(16):
        name = f"pr-{i:02d}"
        git("checkout", "-q", "-b", name, "main")
        if with_bad and i in (5, 11):
            (r / "core.py").write_text(f"def total(xs):\n    return sum(xs) + {i}\n")
        elif with_bad and i == 8:
            (r / "mod_07.py").write_text("CONFLICT = True\n")      # same new file as pr-07
        else:
            if not with_bad and i in (5, 8, 11):
                git("checkout", "-q", "main")
                git("branch", "-D", name)
                continue
            (r / f"mod_{i:02d}.py").write_text(f"VALUE = {i}\n")
        git("add", "."), git("commit", "-qm", name)
        git("checkout", "-q", "main")
        branches.append(name)
    return r, branches


# ---------------------------------------------------------------- GitHub Actions timings

def actions(workflow: str = "ci-shards.yml", limit: int = 5) -> None:
    runs = json.loads(subprocess.run(["gh", "run", "list", "--workflow", workflow, "--limit", str(limit), "--json",
                                      "databaseId,headSha,conclusion"], cwd=ROOT, capture_output=True, text=True,
                                     check=True).stdout)
    out = []
    for run in runs:
        jobs = json.loads(subprocess.run(["gh", "run", "view", str(run["databaseId"]), "--json", "jobs"], cwd=ROOT,
                                         capture_output=True, text=True, check=True).stdout)["jobs"]
        out.append({"run": run["databaseId"], "sha": run["headSha"][:8], "conclusion": run["conclusion"],
                    "jobs": [{"name": j["name"], "started": j["startedAt"], "completed": j["completedAt"],
                              "conclusion": j["conclusion"],
                              "steps": [{"name": s["name"], "started": s.get("startedAt"), "completed": s.get("completedAt")}
                                        for s in j.get("steps", [])]} for j in jobs]})
    _save("actions-timings", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("replay")
    m = sub.add_parser("mutate")
    m.add_argument("modules", nargs="*")
    f = sub.add_parser("flakes")
    f.add_argument("-n", type=int, default=5)
    sub.add_parser("queue")
    a = sub.add_parser("actions")
    a.add_argument("--workflow", default="ci-shards.yml")
    args = ap.parse_args()
    if args.cmd == "replay":
        replay()
    elif args.cmd == "mutate":
        mutate(args.modules or DEFAULT_MUTANTS)
    elif args.cmd == "flakes":
        flakes(args.n)
    elif args.cmd == "queue":
        queue()
    else:
        actions(args.workflow)
    return 0


DEFAULT_MUTANTS = [
    "casebook/redact.py", "casebook/packet.py", "casebook/rules.py", "casebook/policy.py", "casebook/checks.py",
    "casebook/exposure.py", "casebook/models.py", "casebook/gen/updates.py", "casebook/identity/sp.py",
    "casebook/identity/wif.py", "casebook/triage/classify.py", "casebook/sims/s1_outage.py",
]

if __name__ == "__main__":
    raise SystemExit(main())
