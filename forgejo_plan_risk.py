#!/usr/bin/env python3
"""plan-risk on Forgejo (ADR-0128, narthelix/ops#2319): the Terraform plan decides Show or Ask.

The GitHub action (actions/plan-risk) does this with `gh`; infra-terraform moves to Forgejo, where
the same two guards need the forge's own API. The classification is the shared one
(tf_plan_risk.classify); only the forge side differs.

  --mode pr     Ask plan on a PR: label `risk:escalated` and cancel a scheduled merge
                (forgejo_review.escalate). forgejo_risk.py (the `risk` workflow, on the label event)
                then calls the PR Ask, and merger-bot never schedules an Ask PR -- the founder's
                approval is what schedules it. Exit 1 only when the escalation could not land:
                a red required check is then what stops the merge.
  --mode apply  Ask plan at merge time on main: refuse unless the merged PR is Ask
                (`risk:ask` or `risk:escalated`). On GitHub the guard asked "did a person merge
                it"; on Forgejo every merge is gitea-mq's bot (mq-bot), so the question becomes
                "was it Ask" -- an Ask PR is merged only when the founder approved it. What is
                refused is the case the guard exists for: a risky plan behind a Ship/Show merge.

Show plans pass in both modes. Stdlib only. Token: a bot with write on the repo (merger-bot).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error

import forgejo_review
import risk_class
import tf_plan_risk
from forgejo_risk import Api

ASK_LABELS = {"risk:ask", risk_class.ESCALATION_LABEL}


def classify(plan_text: str, sensitive: str) -> tuple[str, list[str]]:
    if sensitive:
        return tf_plan_risk.classify(plan_text, sensitive)
    return tf_plan_risk.classify(plan_text)


def merged_pr(api: Api, sha: str) -> dict | None:
    try:
        return api.call("GET", f"/commits/{sha}/pull")
    except urllib.error.HTTPError as e:
        if e.code == 404:  # noqa: PLR2004 -- no PR merged this commit (a direct push)
            return None
        raise


def run(a: argparse.Namespace, api: Api) -> int:
    with open(a.plan_file, encoding="utf-8") as f:
        cls, reasons = classify(f.read(), a.sensitive)
    print(f"plan risk: {cls} (ADR-0128)")
    for r in reasons:
        print(f"- {r}")
    if cls != "ask":
        return 0

    if a.mode == "pr":
        if not a.pr:
            print("::error::mode=pr needs the PR number")
            return 1
        try:
            forgejo_review.escalate(api, a.pr)
        except (urllib.error.URLError, KeyError, TypeError) as e:
            print(f"::error::plan is Ask but the PR could not be escalated ({e}) -- this red check stops the merge")
            return 1
        print(f"::notice::plan is Ask -- PR #{a.pr} labelled {risk_class.ESCALATION_LABEL}, scheduled merge cancelled")
        return 0

    pr = merged_pr(api, a.sha)
    if pr is None:
        print("::error::the merge-time plan is Ask and no PR merged this commit -- nothing applied")
        return 1
    labels = {x["name"] for x in pr.get("labels") or []}
    if labels & ASK_LABELS:
        print(f"::notice::risky plan, but PR #{pr.get('number')} was Ask -- the founder approved it")
        return 0
    print(f"::error::the merge-time plan is Ask but PR #{pr.get('number')} was not -- nothing applied. "
          "Open a PR so the founder can review the plan.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--mode", required=True, choices=["pr", "apply"])
    ap.add_argument("--plan-file", required=True)
    ap.add_argument("--pr", type=int, default=0)
    ap.add_argument("--sha", default="")
    ap.add_argument("--sensitive", default="")
    a = ap.parse_args()
    token = os.environ.get("FORGEJO_MERGER_TOKEN", "")
    if not token:
        print("::error::FORGEJO_MERGER_TOKEN is empty")
        return 1
    return run(a, Api(a.url, a.repo, token))


if __name__ == "__main__":
    sys.exit(main())
