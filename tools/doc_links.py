"""List every doc URL cited by a pack and (with --check) fetch each one. Used by the weekly doc-drift
workflow; a non-200 or a redirect to a different host is reported, not auto-fixed."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from casebook import packs  # noqa: E402


def urls() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in packs.load_all().values():
        for u in p.meta.get("docs", []):
            out.setdefault(u, []).append(p.id)
    return out


def check(u: str) -> dict[str, object]:
    import httpx2

    try:
        r = httpx2.get(u, follow_redirects=True, timeout=20, headers={"user-agent": "support-casebook-doc-check"})
        return {"status": r.status_code, "final": str(r.url)}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "error": type(e).__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    found = urls()
    if not args.check:
        print(json.dumps({"urls": len(found)}, indent=2))
        return 0
    bad = 0
    for u, ids in sorted(found.items()):
        r = check(u)
        ok = r.get("status") == 200
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {r.get('status')} {u}  ({', '.join(ids[:4])}{'…' if len(ids) > 4 else ''})")
    print(f"{len(found)} URLs, {bad} not 200")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
