#!/usr/bin/env python3
"""Tests for quarantine.py — run with `python3 test_quarantine.py`.

The jest pattern is checked the way jest uses it: a JavaScript RegExp, case-insensitive, tested
against the full name. Python's `re` agrees with it on everything the pattern uses (anchors,
alternation, a negative lookahead, escaped literals).
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import sys
import tempfile

import quarantine as q

ENTRY = {"name": "delete for me: gone at once (from my view)", "file": "src/a.test.tsx",
         "issue": "narthelix/ops#2148", "owner": "claude-code", "until": "2026-11-20"}


def write(body: object) -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "q.json").write_text(json.dumps(body))
    return str(d / "q.json")


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = q.main(argv)
    return rc, out.getvalue()


def main() -> int:
    fails = 0

    def check(name: str, ok: bool, detail: object = ""):
        nonlocal fails
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f": {detail}"))
        fails += not ok

    one = write({"tests": [ENTRY]})
    rc, out = run(["check", one, "--today", "2026-10-21"])
    check("check: a live entry passes", rc == 0, out)
    rc, out = run(["check", one, "--today", "2026-11-21"])
    check("check: an expired entry is red and says what to do", rc == 1 and "ran out" in out, out)
    rc, out = run(["check", one, "--today", "2026-10-01"])
    check("check: more than 30 days ahead is red", rc == 1 and "30 days" in out, out)
    rc, out = run(["check", write({"tests": [{**ENTRY, "owner": ""}]}), "--today", "2026-10-21"])
    check("check: a missing owner is red", rc == 1 and "missing owner" in out, out)
    rc, out = run(["check", write({"tests": [{**ENTRY, "until": "next week"}]}), "--today", "2026-10-21"])
    check("check: a bad date is red", rc == 1 and "YYYY-MM-DD" in out, out)
    rc, out = run(["check", "/nonexistent/quarantine.json"])
    check("check: no file is an empty quarantine", rc == 0, out)
    broken = pathlib.Path(tempfile.mkdtemp()) / "q.json"
    broken.write_text("not json {")
    rc, out = run(["check", str(broken)])
    check("check: broken JSON is red, not silently empty", rc == 1, out)

    two = [ENTRY, {**ENTRY, "name": "a+b [x]"}]
    excl = re.compile(q.jest_pattern(two, only=False), re.I)
    only = re.compile(q.jest_pattern(two, only=True), re.I)
    check("jest: --exclude drops exactly the listed names",
          not excl.search(ENTRY["name"]) and not excl.search("a+b [x]") and excl.search("steady test"))
    check("jest: --exclude keeps a longer name that contains a listed one",
          bool(excl.search(ENTRY["name"] + " and more")))
    check("jest: --only keeps exactly the listed names",
          only.search(ENTRY["name"]) and only.search("a+b [x]") and not only.search("steady test")
          and not only.search("ab [x]"))
    rc, out = run(["jest-pattern", "/nonexistent/q.json", "--exclude"])
    check("jest: empty list prints nothing", rc == 0 and out.strip() == "", out)

    rc, out = run(["files", write({"tests": two})])
    check("files: each file once", out.split() == ["src/a.test.tsx"], out)

    print(f"\n{'FAILED' if fails else 'all passed'} ({fails} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
