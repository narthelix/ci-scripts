#!/usr/bin/env python3
"""Tests for storage_usage.py — run with `python3 test_storage_usage.py`.

The case that carries this tool's value is the counting rule: **a layer shared
between versions is stored once.** Counting blobs per version is the obvious
implementation and it reports a number several times larger than the truth —
measured 2026-09-22, where two versions of one image differed by 20 MB but a
per-version sum would have charged the full 260 MB twice.

The second one is honesty about coverage. The first version of this sweep
printed a confident total while half its packages had silently failed to list,
so an unreached version must appear in the report rather than vanish into it.
"""

from __future__ import annotations

import sys

import storage_usage as su

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


BASE = {"digest": "sha256:base", "size": 200}
APP_1 = {"digest": "sha256:app1", "size": 20}
APP_2 = {"digest": "sha256:app2", "size": 20}

MANIFESTS = {
    "sha256:v1": {"layers": [BASE, APP_1], "config": {"digest": "sha256:cfg1", "size": 1}},
    "sha256:v2": {"layers": [BASE, APP_2], "config": {"digest": "sha256:cfg2", "size": 1}},
    "sha256:idx": {"manifests": [{"digest": "sha256:child", "size": 5}]},
    "sha256:child": {"layers": [BASE], "config": {"digest": "sha256:cfg3", "size": 1}},
}


def fetch(ref):
    return MANIFESTS.get(ref)


def version(name: str):
    return {"name": name}


# --- the counting rule, first on purpose ----------------------------------- #

blobs, seen = su.collect_blobs(fetch, [version("sha256:v1"), version("sha256:v2")])
check(
    "paylasilan katman BIR KEZ sayilir",
    sum(blobs.values()),
    200 + 20 + 20 + 1 + 1,  # not 200 twice
)
check("her iki surum de kapsandi", seen, {"sha256:v1", "sha256:v2"})


# --- an index's children count as covered ---------------------------------- #

blobs, seen = su.collect_blobs(fetch, [version("sha256:idx")])
check("indeksin cocugu kapsanmis sayilir", "sha256:child" in seen, True)
check("cocugun katmanlari sayiya girer", sum(blobs.values()), 5 + 200 + 1)


# --- a manifest the registry does not return is not a zero ------------------ #

blobs, seen = su.collect_blobs(fetch, [version("sha256:gone")])
check("bulunamayan manifest katman uydurmaz", blobs, {})
check("ama surum yine de gorulmus sayilir", seen, {"sha256:gone"})


# --- the report classifies, and admits what it could not reach ------------- #

report, over = su.render(
    artifacts=[(2 * su.GB, "repo-a", 3)],
    packages=[(1 * su.GB, "pkg-a", 10)],
    covered=8,
    versions=10,
    warn_at=2.0,
)
check("esik asildiginda over=True", over, True)
check("olculemeyen surum raporda gorunur", "2/10 surum olculemedi" in report, True)
check("toplam iki bileseni de toplar", "TOPLAM 3.00 GB" in report, True)

report, over = su.render(
    artifacts=[], packages=[(1 * su.GB, "pkg-a", 1)], covered=1, versions=1, warn_at=2.0
)
check("esik altinda over=False", over, False)
check("hepsi olculduyse uyari satiri YOK", "olculemedi" in report, False)

print()
if FAILS:
    print(f"{FAILS} test basarisiz")
    sys.exit(1)
print("hepsi gecti")
