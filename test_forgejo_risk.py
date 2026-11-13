#!/usr/bin/env python3
"""Tests for forgejo_risk.py -- run with `python3 test_forgejo_risk.py`.

What matters: a label event never (re)schedules a merge -- a merge queue that bounces a PR
changes its labels, and re-scheduling then re-queued the same red PR every 30 s (mq-pilot#7,
narthelix/ops#2317); a push or (re)open does schedule; an Ask or escalated PR has a merge that
is already scheduled cancelled, on any event, and a failed cancel is exit 1.
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
    def __init__(self, labels=(), draft=False, cancel_error=None) -> None:
        self.live = list(labels)
        self.draft = draft
        self.cancel_error = cancel_error
        self.calls: list[tuple[str, str]] = []

    def labels(self, pr):
        return list(self.live)

    def call(self, method, path, body=None, raw=False):
        self.calls.append((method, path))
        if path.endswith(".diff"):
            return CODE
        if path.startswith("/labels") and method == "GET":
            return [{"name": f"risk:{c}", "id": i} for i, c in enumerate(("ship", "show", "ask"))]
        if path == "/pulls/7" and method == "GET":
            return {"draft": self.draft}
        if path == "/pulls/7/merge" and method == "DELETE" and self.cancel_error:
            raise self.cancel_error
        return {}


def run(action, **kw) -> tuple[int, bool, bool]:
    api = FakeApi(**kw)
    a = argparse.Namespace(pr=7, action=action, floor="ship", ask_paths="")
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = fr.run(a, api)
    finally:
        sys.stdout = out
    return rc, ("POST", "/pulls/7/merge") in api.calls, ("DELETE", "/pulls/7/merge") in api.calls


for action in ("opened", "synchronize", "reopened", "edited", "ready_for_review", ""):
    check(f"show on '{action or '(no --action)'}' -> scheduled", run(action)[:2], (0, True))
for action in ("labeled", "unlabeled"):
    check(f"show on '{action}' -> NOT rescheduled (the queue loop)", run(action)[:2], (0, False))
check("draft -> not scheduled", run("opened", draft=True)[:2], (0, False))


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, None)


check("escalated by hand on 'labeled' -> scheduled merge cancelled",
      run("labeled", labels=["risk:escalated"]), (0, False, True))
check("ask, nothing scheduled (404) -> ok", run("opened", labels=["risk:escalated"], cancel_error=http(404)),
      (0, False, True))
check("ask, cancel fails -> exit 1", run("labeled", labels=["risk:escalated"], cancel_error=http(500))[0], 1)

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
