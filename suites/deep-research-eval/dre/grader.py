"""Deterministic grading for DeepSearchQA-style answers.

The official evaluation uses an LLM judge with the gold answer. This grader is the free,
reproducible half: it parses the system's FINAL ANSWER, splits list answers into items,
and matches items against the gold list by normalized text. It is deliberately strict
(it can mark a correct paraphrase wrong, never the reverse for exact items), and
`dre judge` compares it with an LLM judge on live runs so its error rate is measured,
not assumed.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

FINAL = re.compile(r"FINAL ANSWER\s*[:：]\s*(.+)", re.I | re.S)
STOP = {"the", "a", "an", "of", "and", "inc", "ltd", "llc", "co", "corp", "university", "nhs", "trust", "foundation"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[\"'`’“”]", "", text)
    text = re.sub(r"[^a-z0-9.%\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).replace("-", " ").split() if t not in STOP}


def split_items(text: str) -> list[str]:
    """Split a list answer on ; , newline or ' and ', but not inside parentheses."""
    text = text.strip().strip(".")
    sep = ";" if ";" in text else ","
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if (ch == sep or ch == "\n") and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    items = []
    for part in out:
        part = re.sub(r"^\s*(and|or|&)\s+", "", part.strip(" -*•\t\n"))
        part = re.sub(r",?\s+(and|or)$", "", part)
        if part and not part.endswith(":") and part.lower() not in {"and", "or", "&"}:     # skip headings such as "Award recipients:"
            items.append(part.strip())
    return items


def match(pred: str, gold: str) -> bool:
    p, g = normalize(pred), normalize(gold)
    if not p or not g:
        return False
    if p == g:
        return True
    tp, tg = tokens(pred), tokens(gold)
    if not tg:
        return False
    if tg <= tp and len(tp) <= 2 * len(tg) + 2:          # gold fully named, not buried in a sentence
        return True
    # numbers must agree exactly: "2019" never matches "2018"
    if {t for t in tg if t[0].isdigit()} != {t for t in tp if t[0].isdigit()}:
        return False
    return len(tp & tg) / len(tp | tg) >= 0.6


@dataclass
class Grade:
    has_final: bool
    predicted: list[str]
    gold: list[str]
    matched: int
    precision: float
    recall: float
    f1: float
    correct: bool                   # fully correct: the headline metric
    kind: str                       # correct | incomplete | extra_items | wrong | no_answer


def grade(response: str, gold_answer: str, answer_type: str) -> Grade:
    m = FINAL.search(response or "")
    has_final = m is not None
    if m:
        final = m.group(1).strip()
    else:                                    # no marker: fall back to the last non-empty line
        lines = [ln for ln in (response or "").strip().splitlines() if ln.strip()]
        final = lines[-1].strip() if lines else ""
    if answer_type.startswith("Single"):
        ok = bool(final) and match(final, gold_answer)
        return Grade(has_final, [final] if final else [], [gold_answer], int(ok), float(ok), float(ok), float(ok), ok,
                     "correct" if ok else ("no_answer" if not final else "wrong"))
    gold = split_items(gold_answer)
    pred = split_items(final) if final else []
    used: set[int] = set()
    hits = 0
    for p in pred:
        for j, g in enumerate(gold):
            if j not in used and match(p, g):
                used.add(j)
                hits += 1
                break
    precision = hits / len(pred) if pred else 0.0
    recall = hits / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    correct = hits == len(gold) == len(pred)
    if not pred:
        kind = "no_answer"
    elif correct:
        kind = "correct"
    elif hits == len(pred):
        kind = "incomplete"            # everything named is right, something is missing
    elif hits == len(gold):
        kind = "extra_items"           # everything is found, plus wrong extras
    else:
        kind = "wrong"
    return Grade(has_final, pred, gold, hits, precision, recall, f1, correct, kind)
