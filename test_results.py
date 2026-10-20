#!/usr/bin/env python3
"""Test results become data (narthelix/ops#2089 T17; handbook "Test coverage and visibility").

Reads the result files a test run already wrote and does two things with them:

    summary  a Markdown block for the job summary: totals, every failure, the slowest cases
    push     one Loki line per test case (and one per run) to a push URL, when one is given

Formats, all written by the test runner itself -- no reporter package is added anywhere:

    JUnit XML   pytest --junitxml, vitest --reporter=junit, Playwright, Maestro --format junit
    jest JSON   jest --json --outputFile (jest has no built-in JUnit reporter)

**Never fails the build.** A result file that cannot be read, or a push that does not arrive, is
a warning. The tests' own exit code is the gate; this only makes their outcome visible, and a
visibility tool that turns CI red is one that gets switched off.

**The push URL is an argument, never a default.** This repository is public (ADR-0097): where
the receiver lives is the caller's to say. The receiver -- not this script -- decides the stream
labels that matter (`job`); the labels sent here are only the low-cardinality ones a dashboard
filters on (repo, suite, event, kind). Per-case detail rides in the line, as JSON.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

MESSAGE_MAX = 500
BATCH = 500


@dataclass
class Case:
    name: str
    file: str
    status: str  # passed | failed | skipped
    seconds: float
    message: str = ""


def _short(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= MESSAGE_MAX else text[: MESSAGE_MAX - 1] + "…"


def parse_junit(text: str) -> list[Case]:
    root = ET.fromstring(text)
    cases = []
    for tc in root.iter("testcase"):
        status, message = "passed", ""
        for child in tc:
            if child.tag in ("failure", "error"):
                status, message = "failed", child.get("message") or child.text or ""
                break
            if child.tag == "skipped":
                status = "skipped"
        name = tc.get("name", "?")
        cls = tc.get("classname", "")
        cases.append(Case(
            name=f"{cls} › {name}" if cls and cls not in name else name,
            file=tc.get("file") or cls,
            status=status,
            seconds=float(tc.get("time") or 0),
            message=_short(message),
        ))
    return cases


def parse_jest(data: dict) -> list[Case]:
    cases = []
    for suite in data.get("testResults", []):
        for a in suite.get("assertionResults", []):
            status = {"passed": "passed", "failed": "failed"}.get(a.get("status"), "skipped")
            cases.append(Case(
                name=" › ".join([*a.get("ancestorTitles", []), a.get("title", "?")]),
                file=suite.get("name", ""),
                status=status,
                seconds=(a.get("duration") or 0) / 1000,
                message=_short("\n".join(a.get("failureMessages") or [])),
            ))
    return cases


def read(path: str) -> list[Case]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.lstrip().startswith("{"):
        return parse_jest(json.loads(text))
    return parse_junit(text)


def collect(patterns: list[str]) -> tuple[list[Case], list[str]]:
    cases, warnings = [], []
    paths = sorted({p for pat in patterns for p in glob.glob(pat, recursive=True)})
    if not paths:
        warnings.append(f"no result file matched {' '.join(patterns)}")
    for p in paths:
        try:
            cases += read(p)
        except (OSError, ValueError, ET.ParseError) as e:
            warnings.append(f"{p}: unreadable ({e.__class__.__name__}: {e})")
    return cases, warnings


def totals(cases: list[Case]) -> dict[str, float]:
    t = {"total": len(cases), "passed": 0, "failed": 0, "skipped": 0}
    for c in cases:
        t[c.status] += 1
    t["seconds"] = round(sum(c.seconds for c in cases), 1)
    return t


def summary(cases: list[Case], title: str, slowest: int = 5) -> str:
    t = totals(cases)
    mark = "❌" if t["failed"] else "✅"
    out = [
        f"## {mark} {title}",
        "",
        "| total | passed | failed | skipped | time |",
        "|---:|---:|---:|---:|---:|",
        f"| {t['total']} | {t['passed']} | {t['failed']} | {t['skipped']} | {t['seconds']} s |",
    ]
    failed = [c for c in cases if c.status == "failed"]
    if failed:
        out += ["", "**Failed**", ""]
        for c in failed:
            first = c.message.splitlines()[0] if c.message else ""
            out.append(f"- `{c.name}` ({c.file})" + (f" — {first}" if first else ""))
    slow = sorted((c for c in cases if c.seconds > 0), key=lambda c: -c.seconds)[:slowest]
    if slow:
        out += ["", "<details><summary>Slowest</summary>", ""]
        out += [f"- {c.seconds:.2f} s `{c.name}`" for c in slow]
        out += ["", "</details>"]
    return "\n".join(out) + "\n"


def streams(cases: list[Case], labels: dict[str, str], context: dict[str, str],
            now_ns: int) -> list[dict]:
    """Loki push bodies: a `run` line, then the cases in batches. Distinct nanosecond stamps keep
    identical lines (same case, same run) from being deduplicated away."""
    run = {**context, **totals(cases)}
    bodies = [{"streams": [{"stream": {**labels, "kind": "run"},
                            "values": [[str(now_ns), json.dumps(run, ensure_ascii=False)]]}]}]
    for start in range(0, len(cases), BATCH):
        values = [
            [str(now_ns + start + i + 1), json.dumps({
                **context, "name": c.name, "file": c.file, "status": c.status,
                "seconds": round(c.seconds, 3), **({"message": c.message} if c.message else {}),
            }, ensure_ascii=False)]
            for i, c in enumerate(cases[start:start + BATCH])
        ]
        bodies.append({"streams": [{"stream": {**labels, "kind": "case"}, "values": values}]})
    return bodies


def push(url: str, bodies: list[dict], timeout: float = 10) -> str | None:
    for body in bodies:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- caller's URL
                if r.status >= 300:
                    return f"push answered {r.status}"
        except OSError as e:
            return f"push failed ({e.__class__.__name__}: {e})"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help="result files or globs (JUnit XML or jest JSON)")
    ap.add_argument("--title", default="Tests")
    ap.add_argument("--summary", help="append the Markdown block here ($GITHUB_STEP_SUMMARY)")
    ap.add_argument("--push-url", default="", help="Loki push URL; empty = no push")
    ap.add_argument("--repo", default="")
    ap.add_argument("--suite", default="unit", help="unit | e2e | ... (a stream label)")
    ap.add_argument("--event", default="", help="pr | main | nightly (a stream label)")
    ap.add_argument("--context", action="append", default=[], metavar="KEY=VALUE",
                    help="extra field on every line (sha, run_url, ref, platform); repeatable")
    a = ap.parse_args(argv)

    cases, warnings = collect(a.files)
    for w in warnings:
        print(f"::warning::test-results: {w}")
    block = summary(cases, a.title)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write("\n" + block)
    t = totals(cases)
    print(f"test-results: {t['total']} case(s), {t['failed']} failed, {t['skipped']} skipped")

    if a.push_url and cases:
        labels = {k: v for k, v in {"repo": a.repo, "suite": a.suite, "event": a.event}.items() if v}
        context = dict(kv.split("=", 1) for kv in a.context if "=" in kv)
        err = push(a.push_url, streams(cases, labels, context, time.time_ns()))
        print(f"::warning::test-results: {err}" if err else "test-results: pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
