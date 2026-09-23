#!/usr/bin/env python3
"""Prune old tagged container versions from an organisation's registry.

A registry with no retention policy is not a disk that fills up — it is a bill
that arrives as a *stopped release*. Measured 2026-09-22 (narthelix/muznara#1758):
a plan's 2 GB of storage is shared between Actions artifacts and **packages**,
every green `main` commit pushes an image, nothing ever removes one, and the
wall was hit inside a release job that had already built and signed its
artifact. (⚠ Corrected 2026-09-23: the first version of this line also counted
Actions *caches*. It should not -- the documentation is explicit that cache
storage is a separate 10 GB per-repository allowance -- and the error mattered,
because 36 GB of caches were being read as part of a 2 GB pool.) The same wall then knocked over a security scan's report upload —
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

⚠ **Untagged versions: only the unreachable ones, and only when reachability
was measured.** Most untagged versions are buildx provenance/attestation
manifests and the per-architecture children of a multi-arch index; deleting one
of those breaks the live image while looking like housekeeping. But an untagged
version that NO surviving tagged version reaches is an orphan, and orphans were
where the storage actually was. Measured 2026-09-23 (narthelix/muznara#1774):
**33.6 GB of orphans against 17.5 GB reachable** -- because the first version of
this script deleted a tagged index and left its children behind (so its
deletions freed almost nothing), and because a moving tag (`v1` rebuilt in
place) orphans the index it moves off. So: reachability is computed from the
versions that SURVIVE this run -- index children plus any manifest whose
`subject` points at one -- and an orphan is deleted only when it is older than
`--orphan-age-hours` (a push in flight uploads children before the tagged index).
If any survivor's manifest cannot be read, that package's orphan pass is
skipped entirely: a reachability set measured with a hole in it would delete a
live image's children.

⚠ **A failed listing stops the run.** A version listing that ends early on an
error looks like a shorter package -- and with orphan deletion, a tagged index
missing from the listing makes its children look unreachable.

**Mirrored packages** (`--mirror HOST`): a package the declarative repository
pulls from HOST is served from there, and this registry is only the tag source
Flux reads plus a fallback. It keeps `--keep-mirrored` newest tagged versions
and loses rule 2 -- the mirror keeps release tags itself. Declared tags stay
protected: a suspended environment still pins this registry.

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
import base64
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"
REGISTRY = "https://ghcr.io"
MANIFEST_TYPES = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class ListingFailed(RuntimeError):
    """A listing or manifest read that failed -- never to be read as 'empty'."""

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


def mirrored_packages(root: pathlib.Path, host: str) -> set[str]:
    """Package names the declarative tree PULLS from `host`.

    Only an `image:` or kustomize `newName:` value counts. Measured on the real
    tree (2026-09-23): a looser match also caught a CI step that PUSHES the
    backup image to the mirror -- and the backup image is the one package that
    must never be treated as served from there, because the mirror's storage is
    what that image restores.
    """
    ref = re.compile(
        r'^\s*-?\s*(?:image|newName):\s*["\']?' + re.escape(host) + r"/([a-z0-9._-]+)"
    )
    found: set[str] = set()
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or not path.is_file() or path.suffix not in MANIFEST_SUFFIXES:
            continue
        for line in path.read_text(errors="replace").splitlines():
            match = ref.match(line)
            if match:
                found.add(match.group(1))
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
        if status != 200 or not isinstance(body, list):
            raise ListingFailed(f"{package} sayfa {page}: {status}")
        if not body:
            return out
        out += body
    raise ListingFailed(f"{package}: 2000 surumden fazlasi -- liste tam okunamadi")


def manifest(org: str, package: str, ref: str, token: str) -> dict[str, Any]:
    # The registry takes the same classic token, base64-encoded.
    req = urllib.request.Request(
        f"{REGISTRY}/v2/{org}/{package}/manifests/{ref}",
        headers={
            "Authorization": f"Bearer {base64.b64encode(token.encode()).decode()}",
            "Accept": MANIFEST_TYPES,
            "User-Agent": "ghcr-prune",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ValueError) as exc:
        raise ListingFailed(f"{package}@{ref}: {exc}") from exc


def tags_of(version: dict[str, Any]) -> list[str]:
    return list(version.get("metadata", {}).get("container", {}).get("tags") or [])


def prunable(
    version_list: list[dict[str, Any]], protected: set[str], keep: int, releases: bool = True
) -> list[dict[str, Any]]:
    """Tagged versions that no rule protects, newest first already removed.

    Untagged versions are never returned here -- see `orphans`. `releases`
    False drops rule 2 (a mirrored package's release tags live in the mirror).
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
        if releases and any(t.startswith("v") for t in names):
            continue
        out.append(version)
    return out


def reachable(fetch, survivors: list[dict[str, Any]]) -> set[str]:
    """Every digest a surviving tagged version reaches: itself and its children.

    `fetch` raises on a failed read; the caller must then skip the orphan pass.
    """
    seen: set[str] = set()
    for version in survivors:
        seen.add(version["name"])
        for child in fetch(version["name"]).get("manifests") or []:
            seen.add(child["digest"])
    return seen


def orphans(
    version_list: list[dict[str, Any]],
    reached: set[str],
    fetch,
    now: dt.datetime,
    min_age: dt.timedelta,
) -> list[dict[str, Any]]:
    """Untagged versions nothing surviving reaches, older than `min_age`.

    A manifest whose `subject` is reached (an OCI referrer -- a signature or an
    SBOM attached by digest) is kept, and so is one that cannot be read.
    """
    out = []
    for version in version_list:
        if tags_of(version) or version["name"] in reached:
            continue
        created = dt.datetime.fromisoformat(version["created_at"].replace("Z", "+00:00"))
        if now - created < min_age:
            continue
        try:
            subject = (fetch(version["name"]).get("subject") or {}).get("digest")
        except ListingFailed:
            continue
        if subject in reached:
            continue
        out.append(version)
    return out


def _delete(org: str, package: str, version: dict[str, Any], token: str) -> bool:
    status, _ = _api(
        f"/orgs/{org}/packages/container/{package}/versions/{version['id']}",
        token,
        method="DELETE",
    )
    # Counted from the response. A counter that counts intentions reports a
    # clean sweep through 403s -- measured, 2026-09-22.
    return status in (204, 200)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", required=True)
    ap.add_argument(
        "--declared",
        required=True,
        help="path to a checkout of the repository that declares what is deployed",
    )
    ap.add_argument("--keep", type=int, default=10, help="newest tagged versions kept per package")
    ap.add_argument("--mirror", help="registry host whose packages are served from there")
    ap.add_argument("--keep-mirrored", type=int, default=2)
    ap.add_argument("--orphan-age-hours", type=int, default=24)
    ap.add_argument("--execute", action="store_true", help="delete (default: dry run)")
    args = ap.parse_args(argv)
    if args.keep < 1 or args.keep_mirrored < 1:
        # keep=0 lets a package lose every tag, and Flux reads tags from here.
        print("--keep ve --keep-mirrored en az 1", file=sys.stderr)
        return 2

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

    mirrored = mirrored_packages(root, args.mirror) if args.mirror else set()
    if args.mirror:
        print(f"{args.mirror} aynasindan cekilen: {', '.join(sorted(mirrored)) or '(yok)'}")

    names = packages(args.org, token)
    if not names:
        print("DURDU: paket listesi bos -- yetki ya da org adi", file=sys.stderr)
        return 2
    print(
        f"{len(names)} paket taraniyor: en yeni {args.keep} etiketli surum kaliyor"
        f" (aynalilarda {args.keep_mirrored}, v* muafiyeti yok);"
        f" {args.orphan_age_hours} saatten eski yetimler siliniyor\n"
    )

    now = dt.datetime.now(dt.timezone.utc)
    min_age = dt.timedelta(hours=args.orphan_age_hours)
    total_seen = total_done = 0
    skipped: list[str] = []
    for name in sorted(names):
        version_list = versions(args.org, name, token)
        is_mirrored = name in mirrored
        tagged = prunable(
            version_list,
            protected,
            args.keep_mirrored if is_mirrored else args.keep,
            releases=not is_mirrored,
        )
        gone = {v["id"] for v in tagged}
        survivors = [v for v in version_list if tags_of(v) and v["id"] not in gone]
        cache: dict[str, dict[str, Any]] = {}

        def fetch(ref: str, _name: str = name) -> dict[str, Any]:
            if ref not in cache:
                cache[ref] = manifest(args.org, _name, ref, token)
            return cache[ref]

        try:
            lost = orphans(version_list, reachable(fetch, survivors), fetch, now, min_age)
        except ListingFailed as exc:
            lost = []
            skipped.append(f"{name} ({exc})")
        candidates = tagged + lost
        if not candidates:
            continue
        total_seen += len(candidates)
        label = f"{name}{' [ayna]' if is_mirrored else ''}"
        if not args.execute:
            print(f"  {label}: {len(tagged)} etiketli + {len(lost)} yetim silinecek"
                  f" ({len(version_list)} surum icinde)")
            continue
        # Tagged first: its children join the orphans only once it is gone.
        done = sum(_delete(args.org, name, v, token) for v in candidates)
        total_done += done
        note = "  <-- HICBIRI SILINMEDI (delete:packages kapsami?)" if done == 0 else ""
        print(f"  {label}: {done}/{len(candidates)} silindi"
              f" ({len(tagged)} etiketli + {len(lost)} yetim){note}")

    for entry in skipped:
        print(f"  ⚠ yetim taramasi ATLANDI: {entry}")
    if args.execute:
        print(f"\nTOPLAM: {total_done}/{total_seen} surum silindi")
        return 1 if (total_seen and not total_done) or skipped else 0
    print(f"\nTOPLAM: {total_seen} surum silinecek (KURU KOSU)")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
