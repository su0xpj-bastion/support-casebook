"""Check the Markdown docs against the code.

- Every relative link resolves to a file or directory.
- Every `#L<n>` link lands on a real line, and when the link text names a function or class
  (e.g. [`rules.classify`](../casebook/rules.py#L33)) that line defines it.
- Every `#heading` link matches a heading in the target file (GitHub slug rules).

    python tools/check_docs.py        # exit 1 on any problem
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)


def slug(heading: str) -> str:
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    text = FENCE.sub("", md.read_text(encoding="utf-8"))
    return {slug(h) for h in re.findall(r"^#+\s+(.*)$", text, re.M)}


def check_line_link(text: str, target: Path, line_no: int) -> str | None:
    lines = target.read_text(encoding="utf-8").splitlines()
    if not 1 <= line_no <= len(lines):
        return f"line {line_no} out of range ({len(lines)} lines)"
    name = re.findall(r"`([^`]+)`", text)
    if not name:
        return None
    ident = name[0].split(".")[-1].split("(")[0]
    if not re.search(rf"\b(def|class)\s+{re.escape(ident)}\b", lines[line_no - 1]):
        return f"line {line_no} does not define {ident!r}: {lines[line_no - 1].strip()[:60]}"
    return None


def main() -> int:
    problems: list[str] = []
    checked = 0
    for md in DOCS:
        body = FENCE.sub("", md.read_text(encoding="utf-8"))
        for text, href in LINK.findall(body):
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            path_part, _, frag = href.partition("#")
            target = (md.parent / path_part).resolve() if path_part else md
            where = f"{md.relative_to(ROOT) if md.is_relative_to(ROOT) else md.name}: [{text}]({href})"
            if not target.exists():
                problems.append(f"{where}: missing {path_part}")
                continue
            if not frag:
                continue
            m = re.fullmatch(r"L(\d+)", frag)
            if m:
                err = check_line_link(text, target, int(m.group(1)))
                if err:
                    problems.append(f"{where}: {err}")
            elif target.suffix == ".md" and frag not in anchors(target):
                problems.append(f"{where}: no heading #{frag}")
    for p in problems:
        print(p)
    print(f"{checked} links checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
