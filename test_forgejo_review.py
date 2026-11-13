#!/usr/bin/env python3
"""Tests for forgejo_review.py -- run with `python3 test_forgejo_review.py`.

What matters: a later push never starts a second review (it passes at once), a reopen does;
a review that did not run is told apart from a clean one (never read as "not critical" and
silently posted); a critical verdict labels risk:escalated AND cancels the scheduled merge,
and a failure there is exit 1; a Claude token in the model's text never reaches a comment.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

import forgejo_review as fr

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


PR = {"draft": False, "user": {"login": "hasan"}, "head": {"sha": "abc"}, "title": "t", "body": "b"}
REVIEWED = [{"body": f"{fr.MARKER}\n**AI review**"}]

check("opened -> review", fr.decide("opened", PR, [])[0], True)
check("synchronize -> pass at once", fr.decide("synchronize", PR, [])[0], False)
check("labeled -> pass", fr.decide("labeled", PR, [])[0], False)
check("draft -> no review", fr.decide("opened", {**PR, "draft": True}, [])[0], False)
check("bot author -> no review", fr.decide("opened", {**PR, "user": {"login": "renovate-bot"}}, [])[0], False)
check("edited after review -> no second review", fr.decide("edited", PR, REVIEWED)[0], False)
check("edited, never reviewed (WIP -> ready) -> review", fr.decide("edited", PR, [])[0], True)
check("reopened after review -> review again", fr.decide("reopened", PR, REVIEWED)[0], True)
check("failed-review comment is not a review",
      fr.decide("edited", PR, [{"body": f"{fr.FAILED_MARKER} could not run"}])[0], True)


def verdict_file(obj) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(obj if isinstance(obj, str) else json.dumps(obj))
    f.close()
    return f.name


OK = {"critical": False, "reason": "fine", "summary": "LGTM", "findings": []}
check("structured output read", fr.read_verdict(verdict_file({"structured_output": OK})), OK)
check("is_error -> did not run", fr.read_verdict(verdict_file({"is_error": True, "structured_output": OK})), None)
check("no structured output -> did not run", fr.read_verdict(verdict_file({"result": "text"})), None)
check("critical not a bool -> did not run",
      fr.read_verdict(verdict_file({"structured_output": {**OK, "critical": "false"}})), None)
check("garbage -> did not run", fr.read_verdict(verdict_file("not json")), None)
check("missing file -> did not run", fr.read_verdict("/nonexistent/verdict.json"), None)

TOKEN = "sk-ant-oat01-" + "x" * 40
leak = {"critical": True, "reason": f"see {TOKEN}", "summary": f"env: {TOKEN}",
        "findings": [{"severity": "critical", "path": "a.py", "line": 3, "body": f"a|b {TOKEN}"}]}
body = fr.render(leak)
check("token redacted from summary, findings, reason", TOKEN in body, False)
check("marker first", body.startswith(fr.MARKER), True)
check("pipe in finding escaped", "a\\|b" in body, True)
check("critical said", "Critical" in body, True)


class FakeApi:
    def __init__(self, fail: dict | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail or {}

    def call(self, method, path, body=None, raw=False):
        self.calls.append((method, path))
        err = self.fail.get((method, path))
        if err:
            raise err
        if path.startswith("/labels") and method == "GET":
            return [{"name": "risk:ask", "id": 1}]
        if path == "/labels" and method == "POST":
            return {"id": 9}
        return {}


def run_publish(obj, fail=None) -> tuple[int, list]:
    api = FakeApi(fail)
    a = argparse.Namespace(pr=7, result=verdict_file(obj), run="")
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        rc = fr.publish(a, api)
    finally:
        sys.stdout = out
    return rc, api.calls


rc, calls = run_publish({"structured_output": OK})
check("clean verdict -> comment only", (rc, [c for c in calls if c[0] != "GET"]), (0, [("POST", "/issues/7/comments")]))
rc, calls = run_publish({"is_error": True})
check("did not run -> says so, exit 0", (rc, calls), (0, [("POST", "/issues/7/comments")]))
CRIT = {"structured_output": {**OK, "critical": True, "reason": "drops a table"}}
rc, calls = run_publish(CRIT)
check("critical -> label created+added, merge cancelled", (rc, ("POST", "/labels") in calls,
      ("POST", "/issues/7/labels") in calls, ("DELETE", "/pulls/7/merge") in calls), (0, True, True, True))


def http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("u", code, "x", {}, None)


rc, _ = run_publish(CRIT, {("DELETE", "/pulls/7/merge"): http(404)})
check("critical, no merge scheduled (404) -> ok", rc, 0)
rc, _ = run_publish(CRIT, {("DELETE", "/pulls/7/merge"): http(500)})
check("critical, cancel fails -> exit 1", rc, 1)
rc, _ = run_publish(CRIT, {("POST", "/issues/7/labels"): http(403)})
check("critical, label fails -> exit 1", rc, 1)

schema = json.loads(json.dumps(fr.SCHEMA))
check("schema requires the verdict fields", sorted(schema["required"]), ["critical", "findings", "reason", "summary"])
check("prompt fills", "{" not in fr.PROMPT.format(repo="r", pr=1, cls="show", title="t", diff=Path("/d"), body="b")
      .replace("{}", ""), True)

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
