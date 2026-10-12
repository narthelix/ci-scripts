#!/usr/bin/env python3
"""Classify a Terraform plan as Show or Ask by what it would DO.

handbook ADR-0128 amendment (narthelix/ops#2055). An infrastructure PR is Show
unless its plan destroys or replaces something, or changes a resource whose
type grants access (IAM, rulesets, branch protection, security lists, keys,
tokens). Anything this script cannot read -- an error, a missing summary --
is Ask: a plan nobody understood is not a safe plan.

Reads the human plan output (`terraform plan -no-color`, or `terraform show
-no-color tfplan`) on stdin; works for remote (HCP) runs, which stream that
same text. Prints `class=<show|ask>` and `reason=` lines. Stdlib only.

Usage:
    terraform plan -no-color | python3 tf_plan_risk.py [--sensitive 'regex ...']
"""

from __future__ import annotations

import argparse
import re
import sys

# `  # google_firebase_web_app.desktop will be created`
ACTION = re.compile(
    r"^\s*# (?P<addr>\S+) (?P<what>will be created|will be updated in-place|will be destroyed"
    r"|must be replaced|will be replaced|will be read during apply|has moved to \S+"
    r"|will be imported|is tainted, so must be replaced)"
)
SUMMARY = re.compile(r"^Plan: \d+ to (import, \d+ to )?add, \d+ to change, \d+ to destroy\.")
NO_CHANGES = re.compile(
    r"^No changes\.|^Your infrastructure matches the configuration\."
    # Only an output value changes: Terraform prints no `Plan:` summary then,
    # just this sentence (measured on infra-terraform#119, the OCI stack's
    # daily `pricing_api_last_check` output). No real resource changes.
    r"|without changing any real infrastructure\."
)
ERROR = re.compile(r"^\s*(│\s*)?Error: ")

DESTRUCTIVE = ("will be destroyed", "must be replaced", "will be replaced", "is tainted, so must be replaced")
CHANGING = DESTRUCTIVE + ("will be created", "will be updated in-place")

# Resource types whose change alters who can do what. Matched against the
# resource TYPE (the address part before the name), not the whole address.
SENSITIVE_TYPES = (
    r"iam|^github_\w*ruleset|branch_protection|security_list|network_security_group"
    r"|firewall|_access_|access_policy|api_token|_token$|service_account_key|_key$|_keys$"
    r"|secret|kms|_policy$|organization_|_member(ship)?$|_permission"
)


def resource_type(addr: str) -> str:
    """`module.x.google_project_iam_member.y["k"]` -> `google_project_iam_member`."""
    parts = re.sub(r"\[[^\]]*\]", "", addr).split(".")
    while parts and parts[0] in ("module", "data"):
        parts = parts[2:] if parts[0] == "module" else parts[1:]
    return parts[0] if parts else addr


def classify(plan: str, sensitive: str = SENSITIVE_TYPES) -> tuple[str, list[str]]:
    lines = plan.splitlines()
    if any(ERROR.search(line) for line in lines):
        return "ask", ["plan reported an error"]
    if not any(SUMMARY.search(line) or NO_CHANGES.search(line) for line in lines):
        return "ask", ["plan summary not found: unreadable plan fails closed"]
    sensitive_rx = re.compile(sensitive)
    reasons: list[str] = []
    for line in lines:
        m = ACTION.match(line)
        if not m:
            continue
        addr, what = m.group("addr"), m.group("what")
        if what in DESTRUCTIVE:
            reasons.append(f"{what}: {addr}")
        elif what in CHANGING and sensitive_rx.search(resource_type(addr)):
            reasons.append(f"access-related resource {what.split()[-1]}: {addr}")
    if reasons:
        return "ask", reasons
    return "show", ["plan only creates or updates non-sensitive resources"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensitive", default=SENSITIVE_TYPES, help="regex over resource types")
    a = ap.parse_args()
    cls, reasons = classify(sys.stdin.read(), a.sensitive)
    print(f"class={cls}")
    for r in reasons[:50]:
        print(f"reason={r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
