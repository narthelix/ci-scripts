#!/usr/bin/env python3
"""Tests for tf_plan_risk.py — run with `python3 test_tf_plan_risk.py`.

The plans below are real shapes from narthelix infrastructure PRs on
2026-10-11/12 (Firebase web apps, Cloudflare cache rule, the gates ruleset
bypass). What must never happen: a plan that destroys, replaces or changes an
access-related resource coming out Show, or an unreadable plan coming out
Show.
"""

from __future__ import annotations

import sys

import tf_plan_risk as tr

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


def cls(text: str) -> str:
    return tr.classify(text)[0]


FIREBASE = """Terraform will perform the following actions:

  # google_firebase_web_app.desktop will be created
  + resource "google_firebase_web_app" "desktop" {
      + display_name    = "Muznara Desktop"
    }

  # google_firebase_web_app.extension will be created
  + resource "google_firebase_web_app" "extension" {
    }

Plan: 2 to add, 0 to change, 0 to destroy.
"""
CACHE_RULE = """  # cloudflare_ruleset.narhel_cache will be updated in-place
  ~ resource "cloudflare_ruleset" "narhel_cache" {
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""
RULESET_BYPASS = """  # github_organization_ruleset.gates will be updated in-place
  ~ resource "github_organization_ruleset" "gates" {
      + bypass_actors {
        }
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""
DESTROY = """  # oci_core_instance.worker will be destroyed
  - resource "oci_core_instance" "worker" {
    }

Plan: 0 to add, 0 to change, 1 to destroy.
"""
REPLACE = """  # google_compute_instance.k3s_agent must be replaced
-/+ resource "google_compute_instance" "k3s_agent" {
    }

Plan: 1 to add, 0 to change, 1 to destroy.
"""
IAM_IN_MODULE = """  # module.wif.google_service_account_iam_member.play["x"] will be created
  + resource "google_service_account_iam_member" "play" {
    }

Plan: 1 to add, 0 to change, 0 to destroy.
"""

check("firebase web apps (2 to add) -> show", cls(FIREBASE), "show")
check("cloudflare cache rule update -> show", cls(CACHE_RULE), "show")
check("no changes -> show", cls("No changes. Your infrastructure matches the configuration.\n"), "show")
check("NEGATIVE CONTROL destroy -> ask", cls(DESTROY), "ask")
check("NEGATIVE CONTROL replace -> ask", cls(REPLACE), "ask")
check("NEGATIVE CONTROL org ruleset change -> ask", cls(RULESET_BYPASS), "ask")
check("NEGATIVE CONTROL iam member inside a module -> ask", cls(IAM_IN_MODULE), "ask")
check("security list update -> ask",
      cls("  # oci_core_security_list.prod will be updated in-place\nPlan: 0 to add, 1 to change, 0 to destroy.\n"), "ask")
check("branch protection create -> ask",
      cls('  # github_branch_protection.default["x"] will be created\nPlan: 1 to add, 0 to change, 0 to destroy.\n'), "ask")
check("custom iam role -> ask",
      cls("  # google_project_iam_custom_role.r will be created\nPlan: 1 to add, 0 to change, 0 to destroy.\n"), "ask")
check("data source read is not a change",
      cls("  # data.google_iam_policy.x will be read during apply\nPlan: 0 to add, 0 to change, 0 to destroy.\n"), "show")
check("empty input fails closed", cls(""), "ask")
check("truncated plan (no summary) fails closed", cls(FIREBASE.split("Plan:")[0]), "ask")
check("plan with an error fails closed", cls(FIREBASE + "\n│ Error: Unauthorized\n"), "ask")
check("dns record create -> show",
      cls("  # cloudflare_dns_record.narhel_downloads will be created\nPlan: 1 to add, 0 to change, 0 to destroy.\n"), "show")
check("resource_type strips module and index",
      tr.resource_type('module.a.module.b.google_project_iam_member.x["k"]'), "google_project_iam_member")
check("reasons name the resource", "will be destroyed: oci_core_instance.worker" in tr.classify(DESTROY)[1], True)

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
