#!/usr/bin/env python3
"""Tests for dead_jobs.py — run with `python3 test_dead_jobs.py`.

The case that carries this tool's value is the **negative** one: a single red
run that healed by itself must not be reported. On the day this was written four
repositories had exactly that — CI runners saturated by one batch of pushes,
green before and green after — and a scanner that named them would have produced
a channel nobody reads, which is the failure this tool exists to prevent. So
that case is asserted first, and asserted on the class, not just on a count.

The other two that are easy to get wrong: a `workflow_call`-only file has no
runs of its own and is **not** dead (seven of eight findings were this false
alarm before it was handled), and an in-flight run reports `conclusion == ""`,
not `None`, so a naive default scores "still running" as a verdict.
"""

from __future__ import annotations

import sys

import dead_jobs as dj


def run(concl, event="schedule", jobs=None, created="2026-09-01T00:00:00Z", rid=1):
    d = {"conclusion": concl, "event": event, "created_at": created, "id": rid}
    if jobs is not None:
        d["jobs_count"] = jobs
    return d


FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


# --- the negative control, first on purpose ------------------------------- #

check(
    "tek seferlik kirmizi, sonrasi yesil -> raporlanmaz",
    dj.classify(
        [run("cancelled"), run("success"), run("success")],
        is_reusable=False,
        window_full=False,
    )[0],
    dj.LIVE,
)

check(
    "en son kosu kirmizi ama pencerede yesil var -> raporlanmaz",
    dj.classify(
        [run("failure"), run("success")], is_reusable=False, window_full=True
    )[0],
    dj.LIVE,
)

# --- reusable workflows are not dead -------------------------------------- #

check(
    "workflow_call-only, sifir kosu -> reusable",
    dj.classify([], is_reusable=True, window_full=False)[0],
    dj.REUSABLE,
)

check(
    "sifir kosu, reusable degil -> never-ran",
    dj.classify([], is_reusable=False, window_full=False)[0],
    dj.NEVER_RAN,
)

# --- the findings we do want ---------------------------------------------- #

check(
    "pencere dolu ve hic yesil yok -> dead",
    dj.classify(
        [run("failure") for _ in range(10)], is_reusable=False, window_full=True
    )[0],
    dj.DEAD,
)

check(
    "tum gecmisi kirmizi (pencere dolmamis) -> never-green",
    dj.classify(
        [run("failure"), run("failure")], is_reusable=False, window_full=False
    )[0],
    dj.NEVER_GREEN,
)

check(
    "sifir job'li kirmizi -> startup-failure",
    dj.classify(
        [run("failure", jobs=0), run("failure")],
        is_reusable=False,
        window_full=False,
    )[0],
    dj.STARTUP_FAILURE,
)

# --- pull_request runs are somebody else's problem ------------------------ #

check(
    "yalnizca pull_request kosulari -> pr-only",
    dj.classify(
        [run("failure", event="pull_request")], is_reusable=False, window_full=False
    )[0],
    dj.PR_ONLY,
)

check(
    "PR kirmizisi, schedule yesili -> raporlanmaz",
    dj.classify(
        [run("failure", event="pull_request"), run("success")],
        is_reusable=False,
        window_full=False,
    )[0],
    dj.LIVE,
)

# --- the API's own traps -------------------------------------------------- #

check(
    'devam eden kosu (conclusion == "") puanlanmaz',
    len(dj.scored([run(""), run("success")])),
    1,
)

check(
    "conclusion None da puanlanmaz",
    len(dj.scored([run(None), run("failure")])),
    1,
)

check(
    "devam eden tek kosu -> pr-only degil, kosu yok gibi degerlendirilir",
    dj.classify([run("")], is_reusable=False, window_full=False)[0],
    dj.PR_ONLY,
)

# --- rendering ------------------------------------------------------------ #

check(
    "bulgu yoksa metin acikca soyler",
    "kimseyi durdurmayan kirmizi yok" in dj.render_text([], 34, "acme"),
    True,
)

out = dj.render_text(
    [
        dj.Finding("r1", "w1", "p1", dj.DEAD, "3 kosu", "2026-08-29"),
        dj.Finding("r2", "w2", "p2", dj.NEVER_GREEN, "6 kosu", "2026-09-14"),
    ],
    34,
    "acme",
)
check("never-green once yazilir", out.index("never-green") < out.index("[dead]"), True)
check("en eski kirmizinin tarihi gorunur", "2026-08-29" in out, True)

print()
if FAILS:
    print(f"{FAILS} test basarisiz")
    sys.exit(1)
print("hepsi gecti")
