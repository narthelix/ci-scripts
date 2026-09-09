#!/usr/bin/env python3
"""Tests for docs_check.py — run with `python3 test_docs_check.py`.

Every case here is a **measured** one: each false positive listed was produced
by a real file in this organisation's repositories during the 2026-09-09 sweep,
not imagined. A link checker that cries wolf gets switched off, so the
suppressions matter more than the detections and are tested first.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import docs_check


def build(tmp: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, body in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp


def run(files: dict[str, str]) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        root = build(pathlib.Path(d), files)
        return docs_check.check_links(root.resolve())


CASES: list[tuple[str, dict[str, str], int]] = [
    (
        "resolving link passes",
        {"a.md": "[x](b.md)", "b.md": "hi"},
        0,
    ),
    (
        "missing target is reported",
        {"a.md": "[x](nope.md)"},
        1,
    ),
    (
        "external links are ignored",
        {"a.md": "[x](https://example.com/y.md) [m](mailto:a@b.c) [f](#frag)"},
        0,
    ),
    (
        "anchor is stripped before resolving",
        {"a.md": "[x](b.md#section)", "b.md": "hi"},
        0,
    ),
    # Measured false positive #1: a guide about linking shows example syntax.
    (
        "inline code span is not a link",
        {"a.md": "Use `[x](../a/b.md)` for portable links."},
        0,
    ),
    (
        "fenced block is not a link",
        {"a.md": "```\n[x](../gone/b.md)\n```\n"},
        0,
    ),
    # Measured false positive #2: Turkish filenames arrive percent-encoded.
    (
        "percent-encoded target is decoded",
        {"a.md": "[x](Uygulama%20G%C3%B6rsel.md)", "Uygulama Görsel.md": "hi"},
        0,
    ),
    # Measured false positive #3: 53 findings came from vendored gems alone.
    (
        "vendored trees are skipped",
        {"vendor/bundle/x.md": "[x](nope.md)"},
        0,
    ),
    (
        "the checker's own checkout is skipped",
        {".ci-scripts/README.md": "[x](nope.md)"},
        0,
    ),
    (
        "escaping the repo is reported as cross-repo",
        {"a.md": "[x](../other-repo/b.md)"},
        1,
    ),
]


def main() -> int:
    failures = 0
    for name, files, expected in CASES:
        got = run(files)
        if len(got) != expected:
            failures += 1
            print(f"FAIL  {name}: expected {expected} problem(s), got {len(got)}")
            for g in got:
                print(f"        {g}")
        else:
            print(f"ok    {name}")

    # The cross-repo case must say *why*, not just "missing": the fix is a
    # different kind of edit (use an absolute URL), so the message is the
    # feature.
    msg = run({"a.md": "[x](../other-repo/b.md)"})[0]
    if "outside the repository" not in msg:
        failures += 1
        print(f"FAIL  cross-repo message is unhelpful: {msg}")
    else:
        print("ok    cross-repo message names the fix")

    # Budget check: under, over, and missing file.
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "ledger.md").write_text("x" * 100, encoding="utf-8")
        for budget, expected, label in ((200, 0, "under budget"), (50, 1, "over budget")):
            got = docs_check.check_ledger(root, "ledger.md", budget)
            if len(got) != expected:
                failures += 1
                print(f"FAIL  ledger {label}: expected {expected}, got {len(got)}")
            else:
                print(f"ok    ledger {label}")
        if len(docs_check.check_ledger(root, "absent.md", 10)) != 1:
            failures += 1
            print("FAIL  ledger missing file should be reported")
        else:
            print("ok    ledger missing file is reported")

    # Config file: the budget must have exactly one home, so a repo's
    # .docs-check.json is what both CI and the git hook read.
    import json
    import subprocess

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "led.md").write_text("x" * 100, encoding="utf-8")
        (root / ".docs-check.json").write_text(
            json.dumps({"ledger_path": "led.md", "ledger_budget_bytes": 50}),
            encoding="utf-8",
        )
        here = pathlib.Path(__file__).parent / "docs_check.py"
        over = subprocess.run(
            [sys.executable, str(here), "--root", d], capture_output=True, text=True
        )
        if over.returncode != 1 or "exceeds the 50 B budget" not in over.stdout:
            failures += 1
            print(f"FAIL  config budget not applied: rc={over.returncode}")
        else:
            print("ok    config file supplies the budget")

        # An explicit flag still wins, so a one-off run can override.
        under = subprocess.run(
            [sys.executable, str(here), "--root", d, "--ledger-budget-bytes", "500"],
            capture_output=True, text=True,
        )
        if under.returncode != 0:
            failures += 1
            print(f"FAIL  CLI should override config: rc={under.returncode}")
        else:
            print("ok    CLI flag overrides the config file")

    print(f"\n{'FAILED' if failures else 'all passed'} ({failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
