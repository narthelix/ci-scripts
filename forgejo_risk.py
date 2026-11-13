#!/usr/bin/env python3
"""Risk class + merge of Ship/Show for a Forgejo pull request (ADR-0128 on Forgejo, ADR-0153 G2f-2,
narthelix/ops#2315). The Forgejo half of actions/risk: same classifier (risk_class.py), same
rule -- the class comes from the changed paths, never from the author; Ship and Show merge
themselves once the required checks pass; Ask is left to a human.

Stdlib only. The forge's address is an argument: this repository is public and holds no host.

    forgejo_risk.py --url URL --repo OWNER/NAME --pr N [--action A] [--floor ship|show|ask] [--ask-paths "re re"]
    token: FORGEJO_MERGER_TOKEN (a non-admin bot with write on the repo; labels + merge)

Order, and why:
  1. Read the PR's LIVE labels (an escalation label, `risk:escalated`, wins).
  2. Classify its diff (pulls/N.diff -> forgejo_pr_files.split -> risk_class.classify).
  3. Set exactly one risk:<class> label.
  4. Ship/Show, not a draft, and the event is one that SCHEDULES (opened, reopened,
     synchronize, edited, ready_for_review -- `--action`): schedule "merge when checks succeed"
     (squash). Then read the labels AGAIN -- an escalation that landed in between cancels the
     scheduled merge. If that cancel fails, exit 1: a red check is then the only thing between
     that PR and a merge.
     A label event only re-labels. That is GitHub's auto-merge, enabled once: a merge queue
     (gitea-mq) that bounces a PR cancels its merge and changes its labels, and re-scheduling on
     that label event re-queued the same red PR every 30 s (measured 2026-11-13, mq-pilot#7,
     narthelix/ops#2317). The author's next push schedules it again.
  5. Ask (or escalated) on any event: cancel a merge that is already scheduled -- an escalation
     label added by hand must stop it as the AI review's does.
Anything else that fails is a warning and exit 0: a labelling hiccup must not turn every PR red,
and without a label nothing is merged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import forgejo_pr_files
import risk_class

COLORS = {"ship": "#0e8a16", "show": "#fbca04", "ask": "#d93f0b"}
# Events that (re)schedule a Ship/Show merge. Empty = a caller that predates --action: as before.
SCHEDULE_ACTIONS = {"", "opened", "reopened", "synchronize", "edited", "ready_for_review"}


class Api:
    def __init__(self, url: str, repo: str, token: str) -> None:
        self.base = f"{url.rstrip('/')}/api/v1/repos/{repo}"
        self.token = token

    def call(self, method: str, path: str, body: dict | list | None = None, raw: bool = False):
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"token {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- the forge's API
            data = r.read()
        if raw:
            return data.decode("utf-8", "replace")
        return json.loads(data) if data else None

    def labels(self, pr: int) -> list[str]:
        return [x["name"] for x in self.call("GET", f"/issues/{pr}/labels")]


def ensure_label_ids(api: Api) -> dict[str, int]:
    have = {x["name"]: x["id"] for x in api.call("GET", "/labels?limit=100")}
    for cls, color in COLORS.items():
        name = f"risk:{cls}"
        if name not in have:
            made = api.call("POST", "/labels", {"name": name, "color": color})
            have[name] = made["id"]
    return have


def cancel_scheduled(api: Api, pr: int, why: str) -> int:
    """Cancel a scheduled merge, if any. 404 = none was scheduled. Exit 1 when it cannot."""
    try:
        api.call("DELETE", f"/pulls/{pr}/merge")
    except urllib.error.HTTPError as e:
        if e.code == 404:  # noqa: PLR2004
            print(f"merge: not scheduled ({why}) -- a human merges")
            return 0
        print(f"::error::{why} but the scheduled merge could not be cancelled: {e}")
        return 1
    except urllib.error.URLError as e:
        print(f"::error::{why} but the scheduled merge could not be cancelled: {e}")
        return 1
    print(f"merge: scheduled merge cancelled ({why}) -- a human merges")
    return 0


def run(a: argparse.Namespace, api: Api) -> int:
    pr = api.call("GET", f"/pulls/{a.pr}")
    live = api.labels(a.pr)
    files = [(f["status"], f["filename"], f["patch"]) for f in forgejo_pr_files.split(api.call("GET", f"/pulls/{a.pr}.diff", raw=True))]
    cls, reasons = risk_class.classify(files, live, a.ask_paths.split(), a.floor)
    print(f"class={cls}")
    for r in reasons[:50]:
        print(f"reason={r}")

    ids = ensure_label_ids(api)
    for other in ("ship", "show", "ask"):
        if other != cls and f"risk:{other}" in live:
            api.call("DELETE", f"/issues/{a.pr}/labels/{ids[f'risk:{other}']}")
    if f"risk:{cls}" not in live:
        api.call("POST", f"/issues/{a.pr}/labels", {"labels": [ids[f"risk:{cls}"]]})

    if cls == "ask":
        return cancel_scheduled(api, a.pr, "Ask")
    if pr.get("draft"):
        print("merge: not scheduled (draft)")
        return 0
    if a.action not in SCHEDULE_ACTIONS:
        print(f"merge: not (re)scheduled on '{a.action}' -- only a push or (re)open schedules it")
        return 0
    api.call(
        "POST",
        f"/pulls/{a.pr}/merge",
        {"Do": "squash", "merge_when_checks_succeed": True, "delete_branch_after_merge": True},
    )
    print("merge: scheduled -- squash when the required checks succeed")
    # An escalation may have landed while the merge was being scheduled.
    after = api.labels(a.pr)
    if risk_class.ESCALATION_LABEL in after or "risk:ask" in after:
        return cancel_scheduled(api, a.pr, "escalated to Ask meanwhile")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--action", default="", help="the pull_request event's action")
    ap.add_argument("--floor", default="ship", choices=list(risk_class.ORDER))
    ap.add_argument("--ask-paths", default="")
    a = ap.parse_args()
    token = os.environ.get("FORGEJO_MERGER_TOKEN", "")
    if not token:
        print("::warning::FORGEJO_MERGER_TOKEN is empty -- no label, no merge")
        return 0
    try:
        return run(a, Api(a.url, a.repo, token))
    except (urllib.error.URLError, KeyError, ValueError) as e:
        print(f"::warning::risk class not set: {e} -- nothing is merged without a label")
        return 0


if __name__ == "__main__":
    sys.exit(main())
