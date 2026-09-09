#!/usr/bin/env python3
"""Tests for trace_check.py — run with `python3 test_trace_check.py`.

The two cases that carry this tool's value are **outdated** (the rule moved and
the code did not) and **superseded ADR** (the enforcer outlived its decision).
Both are failures that today produce nothing red at all, so both are asserted on
the message, not only the exit code — a finding nobody can act on is not a
finding.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import trace_check as tc


def build(files: dict[str, str]) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


RULE = "# Rules\n\n## `br~unread-badge~1` — Unread Badge\n\nNeeds: impl\n\nsome maths\n"


def link(rule_text: str, code: dict[str, str]) -> list[str]:
    root = build({"rules.md": rule_text, **code})
    rules, problems = tc.parse_rules([root / "rules.md"])
    return problems + tc.check_link(rules, tc.collect_tags(root))


def main() -> int:
    fails = 0

    def check(name: str, got: list[str], n: int, must_contain: str | None = None):
        nonlocal fails
        if len(got) != n:
            fails += 1
            print(f"FAIL  {name}: expected {n}, got {len(got)}")
            for g in got:
                print(f"        {g}")
            return
        if must_contain and not any(must_contain in g for g in got):
            fails += 1
            print(f"FAIL  {name}: message lacks {must_contain!r} — got {got}")
            return
        print(f"ok    {name}")

    check("matching tag passes",
          link(RULE, {"a.py": "# [impl->br~unread-badge~1]\nx = 1\n"}), 0)

    # The headline case: the rule moved, the code did not.
    check("outdated revision is caught",
          link("## `br~unread-badge~2` — Unread Badge\n\nNeeds: impl\n",
               {"a.py": "# [impl->br~unread-badge~1]\n"}), 1, "outdated")

    # Two genuine problems here, not one: the tag points at nothing, AND the
    # real rule is left uncovered. Both are true and both are actionable.
    check("orphaned tag is caught",
          link(RULE, {"a.py": "# [impl->br~gone~1]\n"}), 2, "orphaned")

    check("declared Needs with no coverage is caught",
          link(RULE, {"a.py": "x = 1\n"}), 1, "Needs")

    # A rule that is deliberately not built yet declares nothing and stays quiet.
    check("no Needs, no coverage, no complaint",
          link("## `br~future-engine~1` — Later\n\ngroomed when built\n", {"a.py": "x=1\n"}), 0)

    check("duplicate rule id is caught",
          link("## `br~dup~1` — A\n\n## `br~dup~1` — B\n", {}), 1, "duplicate")

    check("unknown coverage type is caught",
          link("## `br~x~1` — A\n\nNeeds: impl, banana\n", {"a.py": "# [impl->br~x~1]\n"}),
          1, "unknown coverage type")

    check("a rule id must use the br type",
          link("## `impl~wrong~1` — A\n", {}), 1, "rule ids use the `br` type")

    # Tags live in comments, so they must be found in every language we use.
    multi = link(RULE, {
        "a.dart": "// [impl->br~unread-badge~1]\n",
        "b.sql": "-- [test->br~unread-badge~1]\n",
    })
    check("tags are found in dart and sql comments too", multi, 0)

    # --- ADR direction -----------------------------------------------------
    adr = build({
        "adr/0042-live.md": "# ADR-0042\n\n**Status:** Accepted\n",
        "adr/0005-dead.md": "# ADR-0005\n\n**Status:** **Superseded** by ADR-0096\n",
    })
    code = build({"pyproject.toml": 'name = "guards the thing (ADR-0042)"\n'})
    check("a live ADR reference passes",
          tc.check_adr_refs([code], [adr / "adr"]), 0)

    dead = build({"pyproject.toml": 'name = "guards the thing (ADR-0005)"\n'})
    check("a superseded ADR reference is caught",
          tc.check_adr_refs([dead], [adr / "adr"]), 1, "Superseded")

    missing = build({"pyproject.toml": 'name = "guards the thing (ADR-9999)"\n'})
    check("a nonexistent ADR reference is caught",
          tc.check_adr_refs([missing], [adr / "adr"]), 1, "does not exist")

    # ⚠ Measured case (38 real instances): a moved ADR leaves a STUB behind under
    # the same number in the other directory. The stub has no Status line, so
    # naive last-wins indexing reads it and a superseded decision looks live.
    stubbed = build({
        "hb/0005-dead.md": "# ADR-0005\n\n**Status:** **Superseded** by ADR-0096\n",
        "prod/0005-dead.md": "# ADR-0005 — moved to the handbook\n\nThis stub stays so references resolve.\n",
    })
    check("a stub must not shadow the real ADR's status",
          tc.check_adr_refs([dead], [stubbed / "prod", stubbed / "hb"]), 1, "Superseded")

    # Prose mentioning an ADR is not a fitness function; only contract names are.
    prose = build({"notes.py": '# see ADR-0005 for history\n'})
    check("prose mentioning an ADR is not flagged",
          tc.check_adr_refs([prose], [adr / "adr"]), 0)

    print(f"\n{'FAILED' if fails else 'all passed'} ({fails} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
