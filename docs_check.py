#!/usr/bin/env python3
"""Mechanical documentation checks: local link targets, and an optional ledger budget.

Detects and reports. It never edits a document — narthelix/muznara#623's
standing constraint is "detect and propose, don't rewrite", because the
judgement a rewrite needs (is this sentence an instruction, or the rationale
for one?) is not mechanically available.

Two checks:

1. **Local link targets resolve.** A markdown link whose target is a path must
   point at something that exists. Cross-repo relative paths (`../other-repo/x`)
   fail as such: CI checks out one repository, so they are unverifiable here and
   the convention is an absolute GitHub URL instead.
2. **Ledger budget.** An entry-state document has a byte budget. Crossing it is
   not an error in itself — it is a prompt to decide, so the message says so.

Measured false-positive classes this deliberately handles (2026-09-09 sweep of
the whole workspace, 694 local links):

* **Code spans and fenced blocks are not links.** A guide *about* linking shows
  `[x](../a/b.md)` as an example; following it is a category error. 2 of 11.
* **Targets are percent-encoded.** `Uygulama%20Görsel%20Üretimi.md` exists;
  a checker that skips `unquote` reports a live file as dead. 1 of 11.
* **Vendored trees are not ours.** `vendor/bundle/ruby/**` alone produced 53
  findings, all in third-party gems.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from urllib.parse import unquote

LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "build", "dist",
    ".next", ".dart_tool", "Pods", ".symlinks", ".gradle", "__pycache__",
    "coverage", ".terraform",
}
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "<")


def strip_code(line: str) -> str:
    """Blank out inline code spans so their contents are never read as links."""
    return re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line)


def iter_markdown(root: pathlib.Path):
    for path in sorted(root.rglob("*.md")):
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        yield path


def check_links(root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    for path in iter_markdown(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path}: unreadable ({exc})")
            continue

        in_fence = False
        for lineno, raw in enumerate(lines, 1):
            if FENCE_RE.match(raw):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            for match in LINK_RE.finditer(strip_code(raw)):
                target = match.group(1).strip()
                if target.startswith(EXTERNAL_PREFIXES):
                    continue
                bare = unquote(target.split("#", 1)[0])
                if not bare:
                    continue

                resolved = (path.parent / bare).resolve()
                where = f"{path.relative_to(root)}:{lineno}"

                try:
                    resolved.relative_to(root.resolve())
                except ValueError:
                    problems.append(
                        f"{where}: `{target}` points outside the repository. "
                        "CI checks out one repo, so this cannot be verified — "
                        "use an absolute https://github.com/narthelix/... URL."
                    )
                    continue

                if not resolved.exists():
                    problems.append(f"{where}: `{target}` does not exist")
    return problems


def check_ledger(root: pathlib.Path, rel: str, budget: int) -> list[str]:
    ledger = root / rel
    if not ledger.is_file():
        return [f"{rel}: ledger_path is set but the file does not exist"]
    size = ledger.stat().st_size
    if size <= budget:
        print(f"ledger {rel}: {size} B / {budget} B budget — ok")
        return []
    return [
        f"{rel}: {size} B exceeds the {budget} B budget by {size - budget} B.\n"
        "         This is a prompt to decide, not an instruction to delete. Either\n"
        "         condense the entry view (handbook ways-of-working/documentation_structure.md\n"
        "         — preserve the original, keep unresolved work reachable), or raise\n"
        "         ledger_budget_bytes in this repo's workflow and say why in the PR."
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--ledger-path", default="")
    ap.add_argument("--ledger-budget-bytes", type=int, default=0)
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    problems = check_links(root)
    if args.ledger_path:
        problems += check_ledger(root, args.ledger_path, args.ledger_budget_bytes)

    if not problems:
        print("docs-check: ok")
        return 0

    print(f"docs-check: {len(problems)} problem(s)\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
