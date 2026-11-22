"""A local merge queue: land branches onto a base branch only after the combined result passes.

For each batch of queued branches:
1. In a scratch worktree at the base tip, merge the branches in queue order. A branch that
   conflicts is ejected right away ("conflict") and the rest continue.
2. Run the test command once on the merged result.
3. Pass: move the base branch to the merged commit (compare-and-swap on the old tip, so a base
   that moved underneath the queue is never overwritten).
4. Fail: bisect. Land the left half (recursively), then the right half on top of whatever landed.
   A single branch that fails on its own is ejected ("tests failed").

Batching trades test runs for isolation: with no failures, a batch of 8 costs one run instead of 8;
each failing branch costs about log2(batch) extra runs to find.

Everything is real git and a real test command; nothing is simulated.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class QueueResult:
    landed: list[str] = field(default_factory=list)
    ejected: dict[str, str] = field(default_factory=dict)      # branch -> reason
    test_runs: int = 0
    test_seconds: float = 0.0
    events: list[dict[str, object]] = field(default_factory=list)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def _rev(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


class MergeQueue:
    def __init__(self, repo: Path, base: str, test_cmd: list[str], *, batch_size: int = 4,
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.repo = repo
        self.base = base
        self.test_cmd = test_cmd
        self.batch_size = batch_size
        self._run = run
        self.result = QueueResult()

    # ------------------------------------------------------------------ building candidates
    def _candidate(self, onto: str, branches: list[str]) -> tuple[Path | None, list[str], str]:
        """Worktree at `onto` with `branches` merged in order. Returns (worktree, merged, head sha).
        Conflicting branches are ejected and skipped."""
        wt = Path(tempfile.mkdtemp(prefix="mq-"))
        shutil.rmtree(wt)
        git(self.repo, "worktree", "add", "--detach", str(wt), onto)
        merged = []
        for b in branches:
            r = git(wt, "merge", "--no-ff", "--no-edit", "-m", f"queue: merge {b}", b, check=False)
            if r.returncode != 0:
                git(wt, "merge", "--abort", check=False)
                self.result.ejected[b] = "conflict"
                self.result.events.append({"event": "conflict", "branch": b})
                continue
            merged.append(b)
        return wt, merged, _rev(wt, "HEAD")

    def _drop(self, wt: Path) -> None:
        git(self.repo, "worktree", "remove", "--force", str(wt), check=False)

    def _test(self, wt: Path, branches: list[str]) -> bool:
        t0 = time.monotonic()
        r = self._run(self.test_cmd, cwd=wt, capture_output=True, text=True)
        secs = time.monotonic() - t0
        self.result.test_runs += 1
        self.result.test_seconds += secs
        self.result.events.append({"event": "test", "branches": list(branches), "ok": r.returncode == 0,
                                   "seconds": round(secs, 2)})
        return r.returncode == 0

    def _advance(self, old: str, new: str) -> None:
        # compare-and-swap: fails if the base moved since `old` was read
        git(self.repo, "update-ref", f"refs/heads/{self.base}", new, old)

    # ------------------------------------------------------------------ landing
    def _land(self, branches: list[str]) -> None:
        if not branches:
            return
        tip = _rev(self.repo, f"refs/heads/{self.base}")
        wt, merged, head = self._candidate(tip, branches)
        try:
            if not merged:
                return
            if self._test(wt, merged):
                self._advance(tip, head)
                self.result.landed += merged
                self.result.events.append({"event": "landed", "branches": merged})
                return
        finally:
            self._drop(wt)
        if len(merged) == 1:
            self.result.ejected[merged[0]] = "tests failed"
            self.result.events.append({"event": "ejected", "branch": merged[0]})
            return
        mid = len(merged) // 2
        self._land(merged[:mid])
        self._land(merged[mid:])

    def process(self, queue: list[str]) -> QueueResult:
        for i in range(0, len(queue), self.batch_size):
            self._land(queue[i:i + self.batch_size])
        git(self.repo, "worktree", "prune", check=False)
        return self.result
