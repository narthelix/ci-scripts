#!/usr/bin/env python3
"""Tests for ghcr_prune.py — run with `python3 test_ghcr_prune.py`.

The case that carries this tool's value is the one where it must delete
**nothing**: a live tag that has already fallen out of the newest-N window.
Measured on the real estate 2026-09-22 — of fifteen packages with something
running, fourteen were running their newest image and one was running images at
positions 10, 11 and 12. "Keep the newest ten" alone would have deleted a tag in
use. That case is asserted first.

The second easy mistake is shape blindness: an image tag appears as kustomize's
`newTag:`, as an inline `image: registry/name:tag`, and as a Helm values block
that splits `repository:` from `tag:` over two lines. A sweep written for the
first two silently misses the third — which here was a database image, the worst
one to lose.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

import ghcr_prune as gp

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


def version(vid: int, tags, created: str):
    return {"id": vid, "created_at": created, "metadata": {"container": {"tags": tags}}}


# --- the negative control, first on purpose -------------------------------- #

old_but_live = [
    version(i, [f"main-{i}"], f"2026-09-{i:02d}T00:00:00Z") for i in range(20, 5, -1)
]
kept = gp.prunable(old_but_live, protected={"main-7"}, keep=3)
check(
    "yeni-N penceresinin DISINDA kalan canli tag silinmiyor",
    [v["id"] for v in kept if v["id"] == 7],
    [],
)
check("koruma disindakiler yine de aday", 7 not in [v["id"] for v in kept] and len(kept) > 0, True)


# --- the three protection rules -------------------------------------------- #

check(
    "en yeni N etiketli surum korunur",
    [v["id"] for v in gp.prunable(old_but_live, protected=set(), keep=3)][:1],
    [17],
)
check(
    "v* ile baslayan tag korunur",
    [v["id"] for v in gp.prunable(
        [version(1, ["v1.0.0"], "2026-01-01T00:00:00Z"),
         version(2, ["main-x"], "2026-01-02T00:00:00Z")],
        protected=set(), keep=0)],
    [2],
)


# --- untagged versions are not candidates at all --------------------------- #

mixed = [
    version(1, [], "2026-01-01T00:00:00Z"),
    version(2, None, "2026-01-02T00:00:00Z"),
    version(3, ["main-x"], "2026-01-03T00:00:00Z"),
]
check(
    "etiketsiz surum hicbir kosulda aday degil",
    [v["id"] for v in gp.prunable(mixed, protected=set(), keep=0)],
    [3],
)


# --- shape blindness: three ways a tag is written -------------------------- #

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "kustomization.yaml").write_text(
        'images:\n  - name: api\n    newTag: main-111 # {"$imagepolicy": "flux:api:tag"}\n'
    )
    (root / "deploy.yaml").write_text("    spec:\n      image: ghcr.io/acme/api:main-222\n")
    (root / "helm.yaml").write_text(
        "    image:\n      registry: ghcr.io\n      repository: acme/db\n      tag: pg18-bitnami\n"
    )
    (root / "notes.md").write_text("tag: not-a-manifest\n")
    (root / ".git").mkdir()
    (root / ".git" / "config.yaml").write_text("tag: inside-dot-git\n")
    found = gp.declared_tags(root)

check("kustomize newTag bulunur", "main-111" in found, True)
check("satir ici image referansi bulunur", "main-222" in found, True)
check("helm'in ayri satirdaki tag'i bulunur", "pg18-bitnami" in found, True)
check("manifest olmayan dosya sayilmaz", "not-a-manifest" in found, False)
check(".git icindekiler sayilmaz", "inside-dot-git" in found, False)

print()
if FAILS:
    print(f"{FAILS} test basarisiz")
    sys.exit(1)
print("hepsi gecti")
