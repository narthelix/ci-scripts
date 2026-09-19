#!/usr/bin/env python3
"""Find CI jobs that are red and stop nobody — the failures nothing reports.

A red **pull request** check blocks a merge, so it announces itself. A red
scheduled or event-driven run blocks nothing: the work simply does not happen,
and the board looks the same either way. Measured 2026-09-19 across one
organisation's 77 active workflows: five were dead, one of them for three weeks
and one that had **never been green in its life**. Two of the five had been
found by accident weeks earlier; nobody had asked how many more there were.

**What this reports, and the one thing it deliberately does not.** The threshold
is *no green run in the window*, never *the last run failed*. That distinction is
the whole design: on the day of the measurement, four repositories had a single
red `main` run caused by CI runners being saturated — every one of them had been
green the day before and was green again after. Reporting those would have
produced four notifications nobody needs, on the same day, from one batch of
pushes; a channel that cries about self-healing failures is the disease this tool
is meant to cure, wearing a different hat. So a one-off failure is invisible here
**by choice**, and only a job that cannot recover on its own is named.

**Why a scanner rather than a trigger on each job.** A `workflow_run` hook has to
be installed in every repository, and it shares the blind spot it is meant to
close: when the hook itself dies, nothing reports that either. One scanner can be
asked whether it ran — and the caller can treat *silence* as the alarm, which is
the only arrangement that survives its own failure.

⚠ **Reusable workflows are not dead workflows.** A `workflow_call`-only file has
zero runs of its own — its runs are recorded against the repository that calls
it. The first version of this scan counted seven of them as "never ran", which is
seven false alarms out of eight findings. `on:` is therefore read before a
zero-run workflow is reported.

⚠ **Two measurement traps live in the API itself**, both of which produced a
wrong answer before they were understood:

* `conclusion` is an **empty string**, not `null`, while a run is still going.
  Any `x.conclusion or "?"`-style default silently turns "still running" into a
  value, and jq's `//` does the same thing. Here, a run without a real conclusion
  is dropped rather than scored.
* `total_count` on a filtered `runs?status=` query reports the **unfiltered**
  total. It is never used for counting.

Reports; never edits. Exit code is 0 even with findings — the report is the
output, and a non-zero exit would turn this into a gate that blocks something,
which is precisely what it is not.

Scope: this file is generic. The organisation, the repositories and the window
are arguments with no defaults — where it runs and what it watches are not
facts this repository is allowed to hold.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

API = "https://api.github.com"

# Conclusions that mean "this run did not do its work".
BAD = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}

# Reported classes, worst first. `pr-only` and `live` are deliberately absent:
# they are the two states that need no attention.
DEAD = "dead"
NEVER_GREEN = "never-green"
STARTUP_FAILURE = "startup-failure"
NEVER_RAN = "never-ran"
LIVE = "live"
PR_ONLY = "pr-only"
REUSABLE = "reusable"

REPORTED = (NEVER_GREEN, STARTUP_FAILURE, DEAD, NEVER_RAN)


@dataclass
class Finding:
    repo: str
    workflow: str
    path: str
    kind: str
    detail: str = ""
    since: str = ""
    runs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "workflow": self.workflow,
            "path": self.path,
            "kind": self.kind,
            "detail": self.detail,
            "since": self.since,
            "runs": self.runs,
        }


def scored(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs that carry a real verdict, newest first.

    Drops pull-request runs (their red blocks a merge and is therefore already
    visible) and anything still in flight. ⚠ An in-flight run has
    `conclusion == ""`, not `None`; both are excluded here on purpose.
    """
    out = []
    for r in runs:
        if r.get("event") == "pull_request":
            continue
        if not r.get("conclusion"):
            continue
        out.append(r)
    return out


def classify(
    runs: list[dict[str, Any]],
    *,
    is_reusable: bool,
    window_full: bool,
) -> tuple[str, str]:
    """Return `(kind, detail)` for one workflow's run history.

    `window_full` says whether the caller fetched as many runs as it asked for;
    it is the only thing separating "has never been green" from "has not been
    green lately", and the difference matters to a reader deciding urgency.
    """
    if not runs:
        # A file with no runs at all is either something nobody triggers, or a
        # reusable workflow whose runs belong to its callers.
        return (REUSABLE, "workflow_call") if is_reusable else (NEVER_RAN, "")

    verdicts = scored(runs)
    if not verdicts:
        return PR_ONLY, "yalnizca pull_request"

    if any(v.get("conclusion") == "success" for v in verdicts):
        return LIVE, ""

    # No green anywhere in what we looked at.
    zero_job = next((v for v in verdicts if v.get("jobs_count") == 0), None)
    if zero_job is not None:
        return STARTUP_FAILURE, "sifir job"

    kind = DEAD if window_full else NEVER_GREEN
    return kind, f"{len(verdicts)} kosu, hicbiri yesil degil"


# --------------------------------------------------------------------------- #
# API layer. Stdlib only: this repository is fetched and run by every repo's
# CI, so a dependency here becomes a dependency everywhere.
# --------------------------------------------------------------------------- #


def _get(path: str, token: str) -> Any:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "narthelix-dead-jobs",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise


def list_repos(org: str, token: str) -> list[str]:
    names, page = [], 1
    while True:
        data = _get(f"/orgs/{org}/repos?per_page=100&page={page}&type=all", token)
        if not data:
            break
        names.extend(r["name"] for r in data if not r.get("archived"))
        if len(data) < 100:
            break
        page += 1
    return names


def is_reusable_workflow(org: str, repo: str, path: str, token: str) -> bool:
    """True when the file's only trigger is `workflow_call`.

    Read only for zero-run workflows, which keeps this to a handful of calls.
    Parsed textually rather than with a YAML library: stdlib only, and the
    question is narrow enough that a parser would be the riskier answer.
    """
    data = _get(f"/repos/{org}/{repo}/contents/{path}", token)
    if not data or "content" not in data:
        return False
    import base64

    try:
        body = base64.b64decode(data["content"]).decode("utf-8", "replace")
    except Exception:
        return False
    triggers = []
    inside = False
    for line in body.splitlines():
        if line.startswith("on:"):
            inside = True
            rest = line[3:].strip()
            if rest:  # `on: [push]` / `on: push`
                triggers.extend(t.strip(" []'\"") for t in rest.split(","))
            continue
        if inside:
            if line[:1] not in (" ", "\t", "#", ""):
                break
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if line.startswith("  ") and not line.startswith("   ") and stripped.endswith(":"):
                triggers.append(stripped[:-1].strip())
            elif line.startswith("- "):
                triggers.append(stripped[2:].strip())
    triggers = [t for t in triggers if t]
    return bool(triggers) and set(triggers) == {"workflow_call"}


def scan_repo(org: str, repo: str, token: str, window: int) -> list[Finding]:
    wfs = _get(f"/repos/{org}/{repo}/actions/workflows?per_page=100", token)
    findings: list[Finding] = []
    for wf in (wfs or {}).get("workflows", []):
        if wf.get("state") != "active":
            continue
        path = wf.get("path", "")
        # GitHub's own managed entries (Dependabot, Pages, Copilot) live under
        # `dynamic/` and are not ours to keep alive.
        if not path.startswith(".github/workflows/"):
            continue
        runs_doc = _get(
            f"/repos/{org}/{repo}/actions/workflows/{wf['id']}/runs?per_page={window}",
            token,
        )
        runs = (runs_doc or {}).get("workflow_runs", [])
        verdicts = scored(runs)

        # Only pay for the jobs call when the workflow is already a candidate:
        # a zero-job run is a startup failure, which looks identical to a normal
        # red from the outside (measured: it shows up under the file path rather
        # than the workflow name).
        if verdicts and not any(v.get("conclusion") == "success" for v in verdicts):
            jobs = _get(f"/repos/{org}/{repo}/actions/runs/{verdicts[0]['id']}/jobs", token)
            verdicts[0]["jobs_count"] = len((jobs or {}).get("jobs", []))

        reusable = False
        if not runs:
            reusable = is_reusable_workflow(org, repo, path, token)

        kind, detail = classify(
            runs, is_reusable=reusable, window_full=len(runs) >= window
        )
        if kind not in REPORTED:
            continue
        since = ""
        if verdicts:
            since = (verdicts[-1].get("created_at") or "")[:10]
        findings.append(
            Finding(
                repo=repo,
                workflow=wf.get("name", path),
                path=path,
                kind=kind,
                detail=detail,
                since=since,
                runs=[str(v.get("conclusion")) for v in verdicts],
            )
        )
    return findings


def render_text(findings: list[Finding], repos: int, org: str) -> str:
    if not findings:
        return f"{org}: {repos} repo tarandi, kimseyi durdurmayan kirmizi yok."
    order = {k: i for i, k in enumerate(REPORTED)}
    rows = sorted(findings, key=lambda f: (order.get(f.kind, 9), f.since))
    lines = [f"{org}: {repos} repo tarandi, {len(rows)} bulgu"]
    for f in rows:
        age = f" (en eski kirmizi {f.since})" if f.since else ""
        extra = f" — {f.detail}" if f.detail else ""
        lines.append(f"  [{f.kind}] {f.repo} / {f.workflow}{extra}{age}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", required=True, help="GitHub organisation to scan")
    ap.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable holding the API token",
    )
    ap.add_argument(
        "--window",
        type=int,
        default=10,
        help="how many recent runs per workflow to look at",
    )
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument(
        "--exclude-repo",
        action="append",
        default=[],
        help="repository to skip; repeatable",
    )
    args = ap.parse_args(argv)

    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"{args.token_env} bos — token olmadan taranamaz", file=sys.stderr)
        return 2

    repos = [r for r in list_repos(args.org, token) if r not in args.exclude_repo]
    findings: list[Finding] = []
    for repo in repos:
        findings.extend(scan_repo(args.org, repo, token, args.window))

    if args.format == "json":
        print(
            json.dumps(
                {
                    "org": args.org,
                    "repos_scanned": len(repos),
                    "findings": [f.as_dict() for f in findings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_text(findings, len(repos), args.org))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
