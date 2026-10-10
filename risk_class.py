#!/usr/bin/env python3
"""Classify a pull request as Ship / Show / Ask from the paths it changes.

handbook ADR-0128 (narthelix/ops#2040). The class decides who merges:
Ship and Show merge themselves on green, Ask waits for the founder. So the
class is computed here, from the diff, never chosen by whoever wants the merge.

The generic rules live here; anything specific to one repository (a module that
holds auth under another name, a repo where every change is Ask) is passed in
by the caller: this repository is public and holds mechanisms, not our values
(ADR-0099).

Usage:
    gh api repos/O/R/pulls/N/files --paginate --jq '.[] | [.status, .filename] | @tsv' \
      | python3 risk_class.py --labels "a,b" --ask-paths 'regex ...' --floor ship
Prints `class=<ship|show|ask>` and one `reason=` line per file that set it.
"""

from __future__ import annotations

import argparse
import re
import sys

ORDER = {"ship": 0, "show": 1, "ask": 2}
ESCALATION_LABEL = "risk:escalated"

# Changes whose blast radius or rollback cost is high. Any match is Ask. Each
# pattern is matched against the repository-relative path. The structural ones
# apply to every file; the name-based one (NAMED_ASK) only to code -- a test
# or a doc page about auth is not the auth code itself.
ASK_PATTERNS: list[tuple[str, str]] = [
    (r"^\.github/(workflows|actions)/", "CI workflow"),
    (r"(^|/)action\.ya?ml$", "CI action"),
    (r"(^|/)(migrations?|alembic)/", "database migration"),
    (r"(^|/)[^/]*(openapi[^/]*external|external[^/]*openapi)[^/]*\.(json|ya?ml)$", "external API contract"),
    (r"(^|/)\.sops\.ya?ml$|\.enc\.(ya?ml|json|env)$", "encrypted secret"),
]
# Words found in real security code across the fleet that the first list
# missed (narthelix/ops#2039, measured 2026-10-10): `security/password.py`,
# `security/permissions.py`, `authentik_jwt.py`, `api_key_resolver.py`,
# `app_check.py`, `id_token.py`, `webhooks/signing.py`, `url_guard.py`,
# `livekit_token.py` -- all came out Show. A false Ask (design `tokens/`)
# costs one founder click; a false Show ships security code unreviewed.
NAMED_ASK = (
    r"(^|/|_|-)(auth|authn|authz|authentication|authorization|oauth|oidc|authentik"
    r"|payments?|billing|secrets?|security|jwt|password|passwords|permissions?"
    r"|api[_-]?keys?|signing|signature|attestation|app[_-]?check|id[_-]?token"
    r"|tokens?|url[_-]?guard|webhooks?|crypto|encryption)(/|_|-|\.)"
)

DOC = re.compile(r"\.(md|mdx|rst|txt)$|(^|/)docs?/|\.(png|jpe?g|gif|svg|webp)$", re.I)
TEST = re.compile(
    r"(^|/)(tests?|__tests__|integration_test|testdata|fixtures)/"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go|dart)$|\.(test|spec)\.[jt]sx?$"
)


def classify(
    files: list[tuple[str, str]],
    labels: list[str] | None = None,
    extra_ask: list[str] | None = None,
    floor: str = "ship",
) -> tuple[str, list[str]]:
    """Return (class, reasons). The highest class wins; nothing lowers one."""
    ask = [(re.compile(p), why) for p, why in ASK_PATTERNS]
    ask += [(re.compile(p), "repository rule") for p in (extra_ask or [])]
    named = re.compile(NAMED_ASK)
    cls, reasons = floor, ([f"repository floor: {floor}"] if floor != "ship" else [])
    if ESCALATION_LABEL in (labels or []):
        return "ask", reasons + [f"escalated ({ESCALATION_LABEL})"]
    if not files:
        return "ask", reasons + ["no changed files reported: fail closed"]

    def raise_to(level: str, why: str) -> None:
        nonlocal cls
        if ORDER[level] > ORDER[cls]:
            cls = level
        if level == "ask" or ORDER[level] >= ORDER[cls]:
            reasons.append(why)

    for status, path in files:
        lowrisk = bool(DOC.search(path) or TEST.search(path))
        hit = next((why for rx, why in ask if rx.search(path)), None)
        if not hit and not lowrisk and named.search(path):
            hit = "auth / payments / secrets"
        if hit:
            raise_to("ask", f"{hit}: {path}")
        elif status == "removed" and not lowrisk:
            raise_to("ask", f"deletion: {path}")
        elif not lowrisk:
            raise_to("show", f"code: {path}")
    return cls, (reasons if cls != "ship" else ["docs and tests only"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="")
    ap.add_argument("--ask-paths", default="", help="space-separated extra regexes")
    ap.add_argument("--floor", default="ship", choices=list(ORDER))
    a = ap.parse_args()
    files = []
    for line in sys.stdin:
        if "\t" in line:
            status, path = line.rstrip("\n").split("\t", 1)
            files.append((status, path))
    cls, reasons = classify(
        files,
        [x.strip() for x in a.labels.split(",") if x.strip()],
        a.ask_paths.split(),
        a.floor,
    )
    print(f"class={cls}")
    for r in reasons[:50]:
        print(f"reason={r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
