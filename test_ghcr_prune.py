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
import datetime as dt
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


# --- orphans: the negative control first ---------------------------------- #
# The expensive mistake is deleting a live image's per-arch child or its
# attestation because it carries no tag. Asserted before anything is deleted.

NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)
DAY = dt.timedelta(hours=24)


def digest_version(vid: int, name: str, tags, created: str):
    v = version(vid, tags, created)
    v["name"] = name
    return v


estate = [
    digest_version(1, "sha256:idx-live", ["main-9"], "2026-09-01T00:00:00Z"),
    digest_version(2, "sha256:arm-live", [], "2026-09-01T00:00:00Z"),
    digest_version(3, "sha256:att-live", [], "2026-09-01T00:00:00Z"),
    digest_version(4, "sha256:arm-orphan", [], "2026-09-01T00:00:00Z"),
    digest_version(5, "sha256:fresh", [], "2026-09-23T06:00:00Z"),
    digest_version(6, "sha256:sig", [], "2026-09-01T00:00:00Z"),
    digest_version(7, "sha256:unreadable", [], "2026-09-01T00:00:00Z"),
]
manifests = {
    "sha256:idx-live": {"manifests": [{"digest": "sha256:arm-live"}, {"digest": "sha256:att-live"}]},
    # Readable on purpose: an unreadable child would be kept by the
    # "unreadable is kept" rule and this control would pass for the wrong reason.
    "sha256:arm-live": {"layers": []},
    "sha256:att-live": {"layers": []},
    "sha256:arm-orphan": {"layers": []},
    "sha256:fresh": {"layers": []},
    "sha256:sig": {"subject": {"digest": "sha256:idx-live"}},
}


def fetch(ref):
    if ref not in manifests:
        raise gp.ListingFailed(ref)
    return manifests[ref]


reached = gp.reachable(fetch, [estate[0]])
lost = [v["id"] for v in gp.orphans(estate, reached, fetch, NOW, DAY)]
check("canli index'in cocuklari (arch + attestation) yetim sayilmaz", [i for i in lost if i in (2, 3)], [])
check("hicbir yerden erisilemeyen eski etiketsiz surum yetim", lost, [4])
check("24 saatten genc yetim silinmez (yolda bir itme olabilir)", 5 in lost, False)
check("subject'i canli olan referrer (imza/SBOM) silinmez", 6 in lost, False)
check("okunamayan aday silinmez", 7 in lost, False)


def broken(ref):
    raise gp.ListingFailed(ref)


try:
    gp.reachable(broken, [estate[0]])
    raised = False
except gp.ListingFailed:
    raised = True
check("kalan bir surum okunamazsa erisilebilirlik olculmus sayilmaz (yukselir)", raised, True)

# The first version's leak: deleting a tagged index must orphan its children
# in the same pass, or the deletion frees nothing.
old_index = digest_version(8, "sha256:idx-old", ["main-1"], "2026-08-01T00:00:00Z")
old_child = digest_version(9, "sha256:arm-old", [], "2026-08-01T00:00:00Z")
manifests["sha256:idx-old"] = {"manifests": [{"digest": "sha256:arm-old"}]}
manifests["sha256:arm-old"] = {"layers": []}
pool = [estate[0], estate[1], estate[2], old_index, old_child]
gone = {v["id"] for v in gp.prunable(pool, protected=set(), keep=1)}
survivors = [v for v in pool if gp.tags_of(v) and v["id"] not in gone]
check("silinen etiketli index", gone, {8})
check(
    "silinen index'in cocugu ayni turda yetim",
    [v["id"] for v in gp.orphans(pool, gp.reachable(fetch, survivors), fetch, NOW, DAY)],
    [9],
)


# --- mirrored packages ----------------------------------------------------- #

releases = [
    version(1, ["v1.0.0"], "2026-01-01T00:00:00Z"),
    version(2, ["main-x"], "2026-01-02T00:00:00Z"),
    version(3, ["v0.9.0"], "2026-01-03T00:00:00Z"),
]
check(
    "aynali pakette v* muafiyeti yok",
    sorted(v["id"] for v in gp.prunable(releases, protected=set(), keep=1, releases=False)),
    [1, 2],
)
check(
    "aynali pakette de gitops'ta pinli tag korunur",
    [v["id"] for v in gp.prunable(releases, protected={"v1.0.0"}, keep=1, releases=False)],
    [2],
)

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "a.yaml").write_text(
        "image: registry.example.com/api:main-1\n"
        "  - name: ghcr.io/acme/web\n    newName: registry.example.com/web\n"
    )
    (root / "b.yaml").write_text("image: ghcr.io/acme/only-ghcr:main-2\n")
    (root / "c.md").write_text("registry.example.com/not-yaml\n")
    # Pushing to the mirror is not pulling from it (the backup-image case).
    (root / "push.yml").write_text(
        "          tags: |\n            registry.example.com/backup:v1\n"
        "# `registry.example.com/commented` bir yorum\n"
        "  url: https://registry.example.com/zot/auth/callback\n"
    )
    got = gp.mirrored_packages(root, "registry.example.com")
check("aynadan cekilenler gitops'tan turetilir", got, {"api", "web"})


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
