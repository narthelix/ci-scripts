#!/usr/bin/env python3
"""Tests for forgejo_risk.py -- run with `python3 test_forgejo_risk.py`.

What matters: a label event never (re)schedules a merge -- a merge queue that bounces a PR
changes its labels, and re-scheduling then re-queued the same red PR every 30 s (mq-pilot#7,
narthelix/ops#2317); a push or (re)open does schedule; an Ask or escalated PR has a merge that
the MERGER scheduled cancelled, on any event, and a failed cancel is exit 1 -- while a merge a
person scheduled on an Ask PR stays (the queue's own label used to cancel it, narthelix/ops#2323).
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.error

import forgejo_risk as fr

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


CODE = "diff --git a/src/x.py b/src/x.py\nindex 1..2 100644\n--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-a\n+b\n"


class FakeApi:
    def __init__(self, labels=(), draft=False, cancel_error=None, timeline=None, diff=None, tags=None) -> None:
        self.diff = diff
        self.tags = tags
        self.timeline = timeline
        self.live = list(labels)
        self.draft = draft
        self.cancel_error = cancel_error
        self.calls: list[tuple[str, str]] = []

    def for_repo(self, repo):
        self.calls.append(("REPO", repo))
        return self

    def labels(self, pr):
        return list(self.live)

    def call(self, method, path, body=None, raw=False):
        self.calls.append((method, path))
        if path.endswith(".diff"):
            return self.diff or CODE
        if path.startswith("/tags"):
            if isinstance(self.tags, Exception):
                raise self.tags
            return self.tags or []
        if path.startswith("/labels") and method == "GET":
            return [{"name": f"risk:{c}", "id": i} for i, c in enumerate(("ship", "show", "ask"))]
        if path == "/pulls/7" and method == "GET":
            return {"draft": self.draft}
        if path.startswith("/issues/7/timeline"):
            if isinstance(self.timeline, Exception):
                raise self.timeline
            page = int(path.rsplit("=", 1)[1])
            return (self.timeline or [])[(page - 1) * 50:page * 50]
        if path == "/pulls/7/merge" and method == "DELETE" and self.cancel_error:
            raise self.cancel_error
        return {}


def run(action, **kw) -> tuple[int, bool, bool]:
    api = FakeApi(**kw)
    a = argparse.Namespace(pr=7, action=action, floor="ship", ask_paths="", merger="merger-bot")
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = fr.run(a, api)
    finally:
        sys.stdout = out
    return rc, ("POST", "/pulls/7/merge") in api.calls, ("DELETE", "/pulls/7/merge") in api.calls


for action in ("opened", "synchronize", "synchronized", "reopened", "edited", "ready_for_review", ""):
    check(f"show on '{action or '(no --action)'}' -> scheduled", run(action)[:2], (0, True))
for action in ("labeled", "unlabeled", "label_updated", "label_cleared"):
    check(f"show on '{action}' -> NOT rescheduled (the queue loop)", run(action)[:2], (0, False))
check("draft -> not scheduled", run("opened", draft=True)[:2], (0, False))


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, None)


check("escalated by hand on 'labeled' -> scheduled merge cancelled",
      run("labeled", labels=["risk:escalated"]), (0, False, True))
check("ask, nothing scheduled (404) -> ok", run("opened", labels=["risk:escalated"], cancel_error=http(404)),
      (0, False, True))
check("ask, cancel fails -> exit 1", run("labeled", labels=["risk:escalated"], cancel_error=http(500))[0], 1)


def ev(kind: str, login: str) -> dict:
    return {"type": kind, "user": {"login": login}}


SCHED, CANCEL = "pull_scheduled_merge", "pull_cancel_scheduled_merge"
ASK = {"labels": ["risk:escalated"]}
check("ask, merge scheduled by the merger -> cancelled",
      run("labeled", timeline=[ev(SCHED, "merger-bot")], **ASK), (0, False, True))
check("ask, merge scheduled by a person, the queue's label event -> kept",
      run("labeled", timeline=[ev("label", "mq-bot"), ev(SCHED, "claude-code"), ev("label", "mq-bot")], **ASK),
      (0, False, False))
check("ask, a person's merge was cancelled and the merger scheduled after -> cancelled",
      run("labeled", timeline=[ev(SCHED, "hasan"), ev(CANCEL, "hasan"), ev(SCHED, "merger-bot")], **ASK),
      (0, False, True))
check("ask, the merger's merge was cancelled and a person scheduled after -> kept",
      run("unlabeled", timeline=[ev(SCHED, "merger-bot"), ev(CANCEL, "merger-bot"), ev(SCHED, "hasan")], **ASK),
      (0, False, False))
check("ask, a person's merge on the second timeline page -> kept",
      run("labeled", timeline=[ev("comment", "x")] * 50 + [ev(SCHED, "hasan")], **ASK), (0, False, False))
check("ask, the timeline cannot be read -> cancelled, as before",
      run("labeled", timeline=http(500), **ASK), (0, False, True))

# A ci-scripts pin bump in the risk workflow (narthelix/ops#2319): Show, and scheduled, only when the
# new commit is one of ci-scripts' v* tags; the tags are read from <owner>/ci-scripts.
NEW = "5678162401a99a34b484ed335105c712d099c4f9"
PINDIFF = (
    "diff --git a/.forgejo/workflows/risk.yml b/.forgejo/workflows/risk.yml\n"
    "--- a/.forgejo/workflows/risk.yml\n+++ b/.forgejo/workflows/risk.yml\n@@ -32,1 +32,1 @@\n"
    "-          CI_SCRIPTS_SHA: 5f3200798867f8ae77ceb9b3692e26770a818adf # ci-scripts v0.23.0\n"
    f"+          CI_SCRIPTS_SHA: {NEW} # ci-scripts v0.23.2\n"
)
TAGS = [{"name": "v0.23.2", "commit": {"sha": NEW}}, {"name": "latest", "commit": {"sha": "1" * 40}}]


def run_pin(tags, action="opened"):
    api = FakeApi(diff=PINDIFF, tags=tags)
    a = argparse.Namespace(pr=7, action=action, floor="ship", ask_paths="", merger="merger-bot", repo="narthelix/x", pin_repo="")
    out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = fr.run(a, api)
    finally:
        sys.stdout = out
    return rc, ("POST", "/pulls/7/merge") in api.calls, ("REPO", "narthelix/ci-scripts") in api.calls


check("ci-scripts pin to a v* tag -> show, scheduled, tags read from <owner>/ci-scripts", run_pin(TAGS), (0, True, True))
check("ci-scripts pin, the commit is no v* tag -> ask, not scheduled",
      run_pin([{"name": "latest", "commit": {"sha": NEW}}])[:2], (0, False))
check("ci-scripts pin, tags cannot be read -> ask, not scheduled", run_pin(http(500))[:2], (0, False))
check("ci-scripts pin on Forgejo's push event -> scheduled", run_pin(TAGS, "synchronized")[:2], (0, True))

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
