#!/usr/bin/env python3
"""Quarantined tests (handbook ADR-0141, narthelix/ops#2089 T28).

A repository lists the tests whose measured flakiness blocks other people's work in one JSON
file (by convention `.github/quarantine.json`):

    {"tests": [{"name": "delete for me: gone at once, from my view only",
                "file": "src/features/chat/chat-safety.test.tsx",
                "issue": "narthelix/ops#2148", "owner": "claude-code", "until": "2026-11-20"}]}

`name` is the test's full name as its runner prints it (jest/vitest: the describe titles and the
test title joined by spaces; Maestro: the flow name). `file` is relative to the repository root.

    check FILE         red when an entry has run out (`until` before today), lacks a field, or is
                       set more than 30 days ahead -- a quarantine is a deadline, not a drawer
    jest-pattern FILE  a `--testNamePattern` regex: --exclude for the gating run, --only for the
                       non-blocking one; prints nothing when the list is empty
    files FILE         the quarantined files, one per line (to narrow the --only run, or to drop
                       Maestro flows from the gating list)

A missing file is an empty quarantine: a repository that never needed one does not carry one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

FIELDS = ("name", "file", "issue", "owner", "until")
MAX_DAYS = 30
# What JavaScript's RegExp treats as syntax (MDN's escapeRegExp set). Python's re.escape also
# escapes spaces, '-', '#', '~' -- identity escapes JS accepts only outside unicode mode.
JS_SPECIAL = re.compile(r"[.*+?^${}()|[\]\\]")


def load(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    return list(data.get("tests") or [])


def problems(entries: list[dict], today: dt.date) -> list[str]:
    out = []
    for e in entries:
        label = e.get("name") or "?"
        missing = [k for k in FIELDS if not str(e.get(k) or "").strip()]
        if missing:
            out.append(f"`{label}`: missing {', '.join(missing)}")
            continue
        try:
            until = dt.date.fromisoformat(e["until"])
        except ValueError:
            out.append(f"`{label}`: until is not YYYY-MM-DD ({e['until']})")
            continue
        if until < today:
            out.append(f"`{label}`: quarantine ran out on {until} ({e['issue']}) -- fix it, extend it "
                       f"with a reason, or delete it with a reason")
        elif (until - today).days > MAX_DAYS:
            out.append(f"`{label}`: until {until} is more than {MAX_DAYS} days ahead")
    return out


def jest_pattern(entries: list[dict], only: bool) -> str:
    """jest and vitest match `-t` as a regex against the full name; anchor it so a quarantined
    name never drops a longer one that merely contains it."""
    if not entries:
        return ""
    names = "|".join(JS_SPECIAL.sub(r"\\\g<0>", e["name"]) for e in entries)
    return f"^(?:{names})$" if only else f"^(?!(?:{names})$)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("file")
    c.add_argument("--today", help="YYYY-MM-DD (tests); default today")
    j = sub.add_parser("jest-pattern")
    j.add_argument("file")
    mode = j.add_mutually_exclusive_group(required=True)
    mode.add_argument("--exclude", action="store_true")
    mode.add_argument("--only", action="store_true")
    f = sub.add_parser("files")
    f.add_argument("file")
    a = ap.parse_args(argv)

    try:
        entries = load(a.file)
    except (OSError, ValueError, AttributeError) as e:
        print(f"::error::quarantine: {a.file} unreadable ({e.__class__.__name__}: {e})")
        return 1
    if a.cmd == "check":
        today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
        found = problems(entries, today)
        for p in found:
            print(f"::error::quarantine: {p}")
        print(f"quarantine: {len(entries)} test(s), {len(found)} problem(s)")
        return 1 if found else 0
    if a.cmd == "jest-pattern":
        print(jest_pattern(entries, a.only))
        return 0
    for path in dict.fromkeys(e.get("file", "") for e in entries if e.get("file")):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
