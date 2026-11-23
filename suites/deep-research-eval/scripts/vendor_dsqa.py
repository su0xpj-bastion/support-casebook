"""Vendor a fixed, stratified slice of DeepSearchQA (google/deepsearchqa, Apache-2.0).

  python -I scripts/vendor_dsqa.py <DSQA-full.csv> <dataset_commit_sha>

Keeps the proportion of Single / Set answers and spreads items across categories.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

SLICE, SEED = 60, 2026
OUT = Path(__file__).resolve().parent.parent / "data"


def main(src: str, sha: str) -> None:
    with open(src, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rng = random.Random(SEED)
    by_type = defaultdict(list)
    for i, r in enumerate(rows):
        by_type[r["answer_type"]].append((i, r))
    picked = []
    for kind, items in sorted(by_type.items()):
        n = round(SLICE * len(items) / len(rows))
        by_cat = defaultdict(list)
        for i, r in items:
            by_cat[r["problem_category"]].append((i, r))
        cats = sorted(by_cat)
        for c in cats:
            rng.shuffle(by_cat[c])
        while n > 0:                                  # round-robin over categories
            for c in cats:
                if n and by_cat[c]:
                    picked.append(by_cat[c].pop())
                    n -= 1
    picked.sort()
    with (OUT / "dsqa_slice.jsonl").open("w", encoding="utf-8") as fh:
        for i, r in picked:
            fh.write(json.dumps({"id": f"dsqa-{i}", "category": r["problem_category"], "answer_type": r["answer_type"],
                                 "problem": r["problem"], "answer": r["answer"]}, ensure_ascii=False) + "\n")
    (OUT / "SOURCE.md").write_text(
        f"`dsqa_slice.jsonl` is a {len(picked)}-item stratified slice (seed {SEED}) of DeepSearchQA by Google DeepMind,\n"
        f"https://huggingface.co/datasets/google/deepsearchqa at commit `{sha}` (file `DSQA-full.csv`, 900 rows).\n"
        "Licensed under the Apache License 2.0. Fields are unchanged; `id` is the row index in the source file.\n"
        "`answer_type` is used only for grading and is never sent to a model, as the dataset card requires.\n",
        encoding="utf-8")
    print(f"{len(picked)} items: " + ", ".join(f"{k} {sum(1 for _, r in picked if r['answer_type'] == k)}" for k in by_type))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
