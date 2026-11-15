#!/usr/bin/env python3
"""Tests for forgejo_plan_risk.py -- run with `python3 test_forgejo_plan_risk.py`.

What matters: a Show plan touches nothing; an Ask plan on a PR labels risk:escalated AND cancels the
scheduled merge, and a failure there is exit 1; at apply time an Ask plan goes through only behind
an Ask PR, never behind a Ship/Show one or with no PR at all.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import urllib.error

import forgejo_plan_risk as fpr

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


SHOW_PLAN = """
  # cloudflare_record.www will be updated in-place
  ~ resource "cloudflare_record" "www" {
Plan: 0 to add, 1 to change, 0 to destroy.
"""
ASK_PLAN = """
  # cloudflare_record.www will be destroyed
  - resource "cloudflare_record" "www" {
Plan: 0 to add, 0 to change, 1 to destroy.
"""


def plan_file(text: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write(text)
    f.close()
    return f.name


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


class FakeApi:
    def __init__(self, pulls: dict | None = None, fail_label: bool = False, schedule: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.pulls = pulls or {}
        self.fail_label = fail_label
        self.schedule = schedule

    def call(self, method: str, path: str, body=None, raw: bool = False):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/labels"):
            return [{"name": "risk:escalated", "id": 7}]
        if method == "POST" and path.endswith("/labels"):
            if self.fail_label:
                raise http_error(500)
            return []
        if method == "DELETE" and path.endswith("/merge"):
            if not self.schedule:
                raise http_error(404)
            return None
        if method == "GET" and path.startswith("/commits/"):
            sha = path.split("/")[2]
            if sha not in self.pulls:
                raise http_error(404)
            return self.pulls[sha]
        raise AssertionError(f"unexpected call {method} {path}")


def args(mode: str, plan: str, pr: int = 0, sha: str = "") -> argparse.Namespace:
    return argparse.Namespace(mode=mode, plan_file=plan_file(plan), pr=pr, sha=sha, sensitive="")


# Show: nothing is touched, in either mode.
api = FakeApi()
check("show plan, pr -> 0", fpr.run(args("pr", SHOW_PLAN, pr=5), api), 0)
check("show plan, pr -> no API call", api.calls, [])
api = FakeApi()
check("show plan, apply -> 0", fpr.run(args("apply", SHOW_PLAN, sha="abc"), api), 0)
check("show plan, apply -> no API call", api.calls, [])

# Ask on a PR: label + cancel the scheduled merge.
api = FakeApi()
check("ask plan, pr -> 0", fpr.run(args("pr", ASK_PLAN, pr=5), api), 0)
check("ask plan, pr -> labelled", ("POST", "/issues/5/labels") in api.calls, True)
check("ask plan, pr -> merge cancelled", ("DELETE", "/pulls/5/merge") in api.calls, True)
api = FakeApi(schedule=False)
check("ask plan, pr, nothing scheduled -> still 0", fpr.run(args("pr", ASK_PLAN, pr=5), api), 0)
api = FakeApi(fail_label=True)
check("ask plan, pr, label fails -> 1 (red check stops the merge)", fpr.run(args("pr", ASK_PLAN, pr=5), api), 1)
check("ask plan, pr without number -> 1", fpr.run(args("pr", ASK_PLAN), FakeApi()), 1)

# Ask at apply time: only behind an Ask PR.
ask_pr = {"number": 9, "labels": [{"name": "risk:ask"}]}
esc_pr = {"number": 9, "labels": [{"name": "risk:escalated"}, {"name": "risk:ask"}]}
show_pr = {"number": 9, "labels": [{"name": "risk:show"}]}
check("ask plan, apply behind Ask PR -> 0", fpr.run(args("apply", ASK_PLAN, sha="a1"), FakeApi({"a1": ask_pr})), 0)
check("ask plan, apply behind escalated PR -> 0", fpr.run(args("apply", ASK_PLAN, sha="a1"), FakeApi({"a1": esc_pr})), 0)
check("ask plan, apply behind Show PR -> 1", fpr.run(args("apply", ASK_PLAN, sha="a1"), FakeApi({"a1": show_pr})), 1)
check("ask plan, apply, no labels -> 1",
      fpr.run(args("apply", ASK_PLAN, sha="a1"), FakeApi({"a1": {"number": 9, "labels": None}})), 1)
check("ask plan, apply, no PR (direct push) -> 1", fpr.run(args("apply", ASK_PLAN, sha="zz"), FakeApi()), 1)

print("\n" + ("HEPSI GECTI" if not FAILS else f"{FAILS} HATA"))
raise SystemExit(1 if FAILS else 0)
