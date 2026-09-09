#!/usr/bin/env python3
"""Typed + versioned traceability checks (ADR-0098).

Connects a written rule to the code that implements it, and a decision to the
fitness function that enforces it. Both links break **silently** today: a rule
can be unimplemented with nothing red, and an enforcer can outlive the decision
it cites — measured 2026-09-09, when ADR-0005 was superseded and every
import-linter contract naming it stayed green.

**Why the id carries a revision.** Bump a rule and existing coverage becomes
`outdated`, mechanically. That is the only mechanical answer to this
workspace's most expensive failure: a sentence that stays *grammatically* true
after the thing it described changed. Nothing is broken, so nothing else
notices.

**Why three modes instead of one gate.** The rule lives in the spec repository
and its implementation lives in the code repository — two private repos, and a
`GITHUB_TOKEN` is scoped to the repo running the workflow. So each CI verifies
the half it can see, and the link itself is checked where both halves are on
disk: a workspace checkout (`mani run trace-check`, the pre-push hook).

    rules   ids are well-formed, unique, and `Needs:` is valid   (spec repo CI)
    tags    coverage tags are well-formed                        (code repo CI)
    link    tags resolve to a real rule at the right revision    (workspace)

It reports; it never edits.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

TYPES = ("br", "adr", "impl", "test")
ID = r"(?P<type>br|adr|impl|test)~(?P<name>[a-z0-9][a-z0-9-]*)~(?P<rev>[1-9][0-9]*)"
ID_RE = re.compile(rf"`{ID}`")
TAG_RE = re.compile(rf"\[(?P<cover>br|adr|impl|test)->{ID}\]")
NEEDS_RE = re.compile(r"^\s*Needs:\s*(?P<list>[a-z, ]+)\s*$", re.M)
CODE_SUFFIXES = {".py", ".dart", ".ts", ".tsx", ".js", ".sql", ".yaml", ".yml", ".toml", ".md"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "build", "dist", ".next",
    ".dart_tool", "Pods", ".symlinks", ".gradle", "__pycache__", "coverage",
    ".terraform", ".ci-scripts",
}


def walk(root: pathlib.Path, suffixes: set[str]):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        if SKIP_DIRS & set(p.relative_to(root).parts):
            continue
        yield p


def parse_rules(paths: list[pathlib.Path]) -> tuple[dict, list[str]]:
    """Return {id_without_rev: {...}} and any problems with the rule file itself."""
    rules: dict[str, dict] = {}
    problems: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        # A rule is a heading carrying an id; `Needs:` applies to the rule above it.
        current = None
        for lineno, line in enumerate(text.split("\n"), 1):
            if line.startswith("#"):
                m = ID_RE.search(line)
                current = None
                if m:
                    if m.group("type") != "br":
                        problems.append(
                            f"{path}:{lineno}: rule ids use the `br` type, got `{m.group('type')}`"
                        )
                        continue
                    key = f"{m.group('type')}~{m.group('name')}"
                    if key in rules:
                        problems.append(
                            f"{path}:{lineno}: duplicate rule id `{key}` "
                            f"(first seen {rules[key]['where']})"
                        )
                        continue
                    current = key
                    rules[key] = {
                        "rev": int(m.group("rev")),
                        "needs": set(),
                        "where": f"{path}:{lineno}",
                    }
                continue
            nm = NEEDS_RE.match(line)
            if nm:
                if current is None:
                    problems.append(f"{path}:{lineno}: `Needs:` with no rule above it")
                    continue
                wanted = {w.strip() for w in nm.group("list").split(",") if w.strip()}
                bad = wanted - set(TYPES)
                if bad:
                    problems.append(
                        f"{path}:{lineno}: unknown coverage type(s) {sorted(bad)}; "
                        f"ADR-0098's vocabulary is {list(TYPES)}"
                    )
                rules[current]["needs"] |= wanted & set(TYPES)
    return rules, problems


def collect_tags(root: pathlib.Path) -> list[dict]:
    tags = []
    for path in walk(root, CODE_SUFFIXES):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for m in TAG_RE.finditer(line):
                tags.append({
                    "cover": m.group("cover"),
                    "key": f"{m.group('type')}~{m.group('name')}",
                    "rev": int(m.group("rev")),
                    "where": f"{path.relative_to(root)}:{lineno}",
                })
    return tags


def check_link(rules: dict, tags: list[dict]) -> list[str]:
    problems = []
    covered: dict[str, set[str]] = {}
    # An outdated tag is still an *attempt* at coverage. Reporting "declares
    # Needs: impl but has no impl coverage" on top of "your tag is outdated"
    # tells the author something false — they clearly wrote one — and doubles
    # the count for a single mistake.
    attempted: dict[str, set[str]] = {}
    for t in tags:
        rule = rules.get(t["key"])
        if rule is None:
            problems.append(
                f"{t['where']}: covers `{t['key']}~{t['rev']}`, which does not exist "
                "(orphaned — the rule was renamed or removed)"
            )
            continue
        if t["rev"] != rule["rev"]:
            attempted.setdefault(t["key"], set()).add(t["cover"])
            problems.append(
                f"{t['where']}: covers `{t['key']}~{t['rev']}` but the rule is at "
                f"revision {rule['rev']} — **outdated**. The rule changed; re-read it "
                "and either update the code or bump the tag once it still holds."
            )
            continue
        covered.setdefault(t["key"], set()).add(t["cover"])

    for key, rule in sorted(rules.items()):
        missing = rule["needs"] - covered.get(key, set()) - attempted.get(key, set())
        if missing:
            problems.append(
                f"{rule['where']}: `{key}~{rule['rev']}` declares Needs: "
                f"{', '.join(sorted(rule['needs']))} but has no {', '.join(sorted(missing))} "
                "coverage. A rule that is deliberately not built yet should not declare it."
            )
    return problems


def check_adr_refs(roots: list[pathlib.Path], adr_dirs: list[pathlib.Path]) -> list[str]:
    """A fitness function naming an ADR must name one that exists and still stands."""
    # ⚠ The same ADR number exists in BOTH directories for 38 decisions: when an
    # ADR moved to the handbook, muznara kept a stub so old references still
    # resolve. A stub is a pointer, not the record — it carries no `**Status:**`
    # line. Naive last-wins indexing reads the stub and every superseded ADR
    # looks live, which is exactly the failure this function exists to catch.
    # So: among the files sharing a number, prefer one that states a Status.
    candidates: dict[str, list[pathlib.Path]] = {}
    for d in adr_dirs:
        for p in d.glob("[0-9]*.md"):
            candidates.setdefault(p.name.split("-")[0], []).append(p)
    known: dict[str, pathlib.Path] = {}
    for num, paths in candidates.items():
        with_status = [
            p for p in paths
            if "**Status:**" in p.read_text(encoding="utf-8", errors="ignore")[:600]
        ]
        known[num] = (with_status or paths)[0]
    problems = []
    for root in roots:
        for path in walk(root, {".toml", ".cfg", ".ini", ".py"}):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.split("\n"), 1):
                if "name =" not in line and "name:" not in line:
                    continue
                for num in re.findall(r"ADR-(\d{4})", line):
                    adr = known.get(num)
                    where = f"{path}:{lineno}"
                    if adr is None:
                        problems.append(f"{where}: cites ADR-{num}, which does not exist")
                        continue
                    head = adr.read_text(encoding="utf-8")[:600]
                    if re.search(r"\*\*Status:\*\*\s*\*\*?Superseded", head):
                        problems.append(
                            f"{where}: cites ADR-{num}, which is **Superseded** "
                            f"({adr.name}). The enforcer outlived its decision — "
                            "re-point it or retire it."
                        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["rules", "tags", "link"])
    ap.add_argument("--rules-file", action="append", default=[])
    ap.add_argument("--code-root", action="append", default=[])
    ap.add_argument("--adr-dir", action="append", default=[])
    args = ap.parse_args()

    problems: list[str] = []
    rules: dict = {}

    if args.mode in ("rules", "link"):
        paths = [pathlib.Path(p) for p in args.rules_file]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            sys.exit(f"rules file(s) not found: {[str(m) for m in missing]}")
        rules, rule_problems = parse_rules(paths)
        problems += rule_problems
        if not rules and args.mode == "rules":
            problems.append("no rule ids found — is the file using ADR-0098's `br~name~rev`?")

    tags: list[dict] = []
    if args.mode in ("tags", "link"):
        for r in args.code_root:
            tags += collect_tags(pathlib.Path(r).resolve())

    if args.mode == "link":
        problems += check_link(rules, tags)
        if args.adr_dir:
            problems += check_adr_refs(
                [pathlib.Path(r).resolve() for r in args.code_root],
                [pathlib.Path(d) for d in args.adr_dir],
            )

    if not problems:
        detail = {
            "rules": f"{len(rules)} rule id(s) valid",
            "tags": f"{len(tags)} coverage tag(s) well-formed",
            "link": f"{len(rules)} rule(s), {len(tags)} tag(s) linked",
        }[args.mode]
        print(f"trace-check ({args.mode}): ok — {detail}")
        return 0

    print(f"trace-check ({args.mode}): {len(problems)} problem(s)\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
