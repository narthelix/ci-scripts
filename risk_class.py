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
    gh api repos/O/R/pulls/N/files --paginate --jq '.[] | {status, filename, patch} | @json' \
      | python3 risk_class.py --labels "a,b" --ask-paths 'regex ...' --floor ship
Prints `class=<ship|show|ask>` and one `reason=` line per file that set it.
One JSON object per line carries the diff (`patch`), which the content rules
read; the older `status<TAB>path` lines are still accepted and, having no
diff, fail closed wherever a content rule would need one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

ORDER = {"ship": 0, "show": 1, "ask": 2}
ESCALATION_LABEL = "risk:escalated"

# Changes whose blast radius or rollback cost is high. Any match is Ask. Each
# pattern is matched against the repository-relative path. The structural ones
# apply to every file; the name-based one (NAMED_ASK) only to code -- a test
# or a doc page about auth is not the auth code itself.
ASK_PATTERNS: list[tuple[str, str]] = [
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

# Judged by EFFECT, not by path (ADR-0128 amendment, narthelix/ops#2055):
# these paths used to be Ask whatever the change. They still are when the
# diff is unreadable, deleted, or does anything but the safe thing below.
CI_PATH = re.compile(r"^\.github/(workflows|actions)/|(^|/)action\.ya?ml$")
MIGRATION_PATH = re.compile(r"(^|/)(migrations?|alembic)/")
# A changed line in a workflow that only moves a pin: `uses: x@<40-hex> # vN`
# or a `version:` value -- what Renovate and the gates SHA sweeps produce.
PIN_LINE = re.compile(r"^\s*(-\s*)?uses:\s*\S+@[0-9a-f]{40}(\s+#.*)?\s*$|^\s*version:\s*\S+\s*$")
# What a migration must not do without the founder: drop, rename, retype,
# rewrite data or run raw SQL (the last because nothing can read it).
DESTRUCTIVE = re.compile(
    r"\bop\.(drop_\w+|alter_column|rename_table|execute|bulk_insert)\b"
    r"|\b(DROP|TRUNCATE|RENAME)\b|\bALTER\s+(TABLE|COLUMN|TYPE)\b|\bDELETE\s+FROM\b"
    r"|\bUPDATE\s+\S+\s+SET\b",
    re.I,
)
# Alembic and most frameworks keep the reverse step in the same file; its
# drops are expected and are not what the upgrade does.
DOWNGRADE = re.compile(r"^\s*(async\s+)?def\s+downgrade\b")


def _changed(patch: str) -> list[str]:
    return [line for line in patch.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def ci_effect(status: str, patch: str | None) -> str | None:
    """None when the change only moves pins; otherwise why it is Ask."""
    if status == "removed":
        return "CI workflow deleted"
    if patch is None:
        return "CI workflow (diff unavailable)"
    lines = [ln[1:] for ln in _changed(patch) if ln[1:].strip()]
    if lines and all(PIN_LINE.match(ln) for ln in lines):
        return None
    return "CI workflow"


def migration_effect(status: str, patch: str | None) -> str | None:
    """None for a NEW migration whose upgrade only creates; otherwise why Ask."""
    if status != "added":
        # An applied migration is history: editing or deleting one is never routine.
        return "database migration edited or removed"
    if patch is None:
        return "database migration (diff unavailable)"
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        code = line[1:]
        if DOWNGRADE.match(code):
            break
        if DESTRUCTIVE.search(code.split("#", 1)[0]):
            return f"destructive migration: {code.strip()[:80]}"
    return None

DOC = re.compile(r"\.(md|mdx|rst|txt)$|(^|/)docs?/|\.(png|jpe?g|gif|svg|webp)$", re.I)
TEST = re.compile(
    r"(^|/)(tests?|__tests__|integration_test|testdata|fixtures)/"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go|dart)$|\.(test|spec)\.[jt]sx?$"
)


def classify(
    files: list[tuple[str, str]] | list[tuple[str, str, str | None]],
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

    for entry in files:
        status, path = entry[0], entry[1]
        patch = entry[2] if len(entry) > 2 else None  # noqa: PLR2004
        lowrisk = bool(DOC.search(path) or TEST.search(path))
        hit = next((why for rx, why in ask if rx.search(path)), None)
        if not hit and CI_PATH.search(path):
            effect = ci_effect(status, patch)
            if effect is None:
                raise_to("show", f"CI pin bump: {path}")
                continue
            hit = effect
        if not hit and MIGRATION_PATH.search(path) and not DOC.search(path):
            effect = migration_effect(status, patch)
            if effect is None:
                raise_to("show", f"additive migration: {path}")
                continue
            hit = effect
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
    files: list[tuple[str, str, str | None]] = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if line.startswith("{"):
            obj = json.loads(line)
            files.append((obj.get("status", ""), obj.get("filename", ""), obj.get("patch")))
        elif "\t" in line:
            status, path = line.split("\t", 1)
            files.append((status, path, None))
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
