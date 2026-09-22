#!/usr/bin/env python3
"""Prune old tagged container versions from an organisation's registry.

A registry with no retention policy is not a disk that fills up — it is a bill
that arrives as a *stopped release*. Measured 2026-09-22 (narthelix/muznara#1758):
a plan's 2 GB of storage is shared between Actions artifacts, Actions caches and
**packages**, every green `main` commit pushes an image, nothing ever removes
one, and the wall was hit inside a release job that had already built and signed
its artifact. The same wall then knocked over a security scan's report upload —
a scan that had itself passed clean. Neither failure named storage in its
headline; both looked like the job's own subject.

**What protects a tag, and why the protected set is deliberately over-broad.**
Three rules, unioned:

1. **Every tag the declarative repository mentions.** Not the cluster: the
   cluster was measured (2026-09-22) and the only tags it holds that the
   declarative repo does not are images of *completed* Job pods, which nothing
   will ever restart. The declarative repo, by contrast, also carries tags that
   are pinned but NOT currently running — a suspended environment's pin is
   exactly the tag a resume would pull, and it would be invisible to the cluster.
2. **Every tag that starts with `v`.** Release tags are the rollback surface.
3. **The newest `--keep` tagged versions of each package.**

Rule 1 reads the repository as TEXT, on purpose. Image tags live in at least
three shapes — kustomize `newTag:`, an inline `image: registry/name:tag`, and a
Helm values block that splits `repository:` from `tag:` across two lines — and a
scan written for two of them silently misses the third. (That happened here: the
first version of this sweep missed a database image whose tag sat alone under a
`tag:` key, which is precisely the image whose loss would be worst.) So every
`tag:`/`newTag:` value in the tree is protected regardless of what it belongs to.
**Over-protecting costs storage; under-protecting costs production.** Measured
cost of that choice on the real tree: 22 protected values against 21 that a
strict YAML walk finds — one extra.

⚠ **Untagged versions are never touched.** Measured: 287 of one package's 440
versions were untagged. Those are buildx provenance/attestation manifests and
the per-architecture children of a multi-arch image; deleting them breaks the
live image while looking like housekeeping. The storage is in the tagged ones.

⚠ **An empty protected set stops the run.** A missing checkout, a renamed
directory or a bad path all produce "nothing is protected", which reads exactly
like "nothing to protect" and would delete the estate. Same reason the run stops
if the package listing comes back empty.

⚠ **Deletion needs its own scope.** `write:packages` is not enough — a token
without `delete:packages` returns 403 on every DELETE, and a counter that counts
*planned* deletions (rather than the ones the API confirmed) will report a clean
sweep anyway. That mistake was made here on 2026-09-22 and is why this script
counts responses, not intentions.

Dry run is the default. `--execute` deletes.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"

#: `newTag: abc` and `tag: abc` -- any key spelling of a tag, whatever owns it.
TAG_KEY = re.compile(r'^\s*-?\s*(?:newTag|tag):\s*["\']?([A-Za-z0-9][A-Za-z0-9._-]*)["\']?\s*(?:#.*)?$')
#: An inline reference: `image: ghcr.io/org/name:tag`.
IMAGE_REF = re.compile(r'[a-z0-9.-]+/[a-z0-9._-]+/([a-z0-9._-]+):([A-Za-z0-9][A-Za-z0-9._-]*)')

MANIFEST_SUFFIXES = {".yaml", ".yml"}


def declared_tags(root: pathlib.Path) -> set[str]:
    """Every tag-looking value in the declarative tree (see rule 1 above)."""
    found: set[str] = set()
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        if path.suffix not in MANIFEST_SUFFIXES:
            continue
        for line in path.read_text(errors="replace").splitlines():
            key = TAG_KEY.match(line)
            if key:
                found.add(key.group(1))
            for _, tag in IMAGE_REF.findall(line):
                found.add(tag)
    return found


def _api(path: str, token: str, method: str = "GET") -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ghcr-prune",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError:
        return 0, None


def packages(org: str, token: str) -> list[str]:
    names: list[str] = []
    for page in range(1, 11):
        status, body = _api(
            f"/orgs/{org}/packages?package_type=container&per_page=100&page={page}", token
        )
        if status != 200 or not isinstance(body, list) or not body:
            break
        names += [p["name"] for p in body]
    return names


def versions(org: str, package: str, token: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 21):
        status, body = _api(
            f"/orgs/{org}/packages/container/{package}/versions?per_page=100&page={page}", token
        )
        if status != 200 or not isinstance(body, list) or not body:
            break
        out += body
    return out


def tags_of(version: dict[str, Any]) -> list[str]:
    return list(version.get("metadata", {}).get("container", {}).get("tags") or [])


def prunable(
    version_list: list[dict[str, Any]], protected: set[str], keep: int
) -> list[dict[str, Any]]:
    """Tagged versions that no rule protects, newest first already removed.

    Untagged versions are not candidates at all -- they are never returned.
    """
    tagged = [v for v in version_list if tags_of(v)]
    tagged.sort(key=lambda v: v["created_at"], reverse=True)
    out = []
    for index, version in enumerate(tagged):
        if index < keep:
            continue
        names = tags_of(version)
        if any(t in protected for t in names):
            continue
        if any(t.startswith("v") for t in names):
            continue
        out.append(version)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", required=True)
    ap.add_argument(
        "--declared",
        required=True,
        help="path to a checkout of the repository that declares what is deployed",
    )
    ap.add_argument("--keep", type=int, default=10, help="newest tagged versions kept per package")
    ap.add_argument("--execute", action="store_true", help="delete (default: dry run)")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN yok", file=sys.stderr)
        return 2

    root = pathlib.Path(args.declared)
    if not root.is_dir():
        print(f"--declared bir dizin degil: {root}", file=sys.stderr)
        return 2

    protected = declared_tags(root)
    if not protected:
        # The expensive failure: this looks like "nothing to protect".
        print(f"DURDU: {root} icinde tek bir tag bulunamadi", file=sys.stderr)
        return 2
    print(f"{len(protected)} tag korunuyor (kaynak: {root})")

    names = packages(args.org, token)
    if not names:
        print("DURDU: paket listesi bos -- yetki ya da org adi", file=sys.stderr)
        return 2
    print(f"{len(names)} paket taraniyor, her pakette en yeni {args.keep} etiketli surum kaliyor\n")

    total_seen = 0
    total_done = 0
    for name in sorted(names):
        version_list = versions(args.org, name, token)
        candidates = prunable(version_list, protected, args.keep)
        if not candidates:
            continue
        total_seen += len(candidates)
        if not args.execute:
            print(f"  {name}: {len(candidates)} silinecek ({len(version_list)} surum icinde)")
            continue
        done = 0
        for version in candidates:
            status, _ = _api(
                f"/orgs/{args.org}/packages/container/{name}/versions/{version['id']}",
                token,
                method="DELETE",
            )
            # Counted from the response. A counter that counts intentions
            # reports a clean sweep through 403s -- measured, 2026-09-22.
            done += status in (204, 200)
        total_done += done
        note = "  <-- HICBIRI SILINMEDI (delete:packages kapsami?)" if done == 0 else ""
        print(f"  {name}: {done}/{len(candidates)} silindi{note}")

    if args.execute:
        print(f"\nTOPLAM: {total_done}/{total_seen} surum silindi")
        return 1 if total_seen and not total_done else 0
    print(f"\nTOPLAM: {total_seen} surum silinecek (KURU KOSU)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
