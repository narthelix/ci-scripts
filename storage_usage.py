#!/usr/bin/env python3
"""Measure what an organisation actually holds in its shared storage quota.

A plan's "storage for Actions and Packages" is a **pooled allowance**, and when
it runs out the failure does not look like storage: a release job that has
already built and signed its artifact dies uploading it, and a security scan
that passed clean dies uploading its report. Both were measured on 2026-09-22
(narthelix/muznara#1576, #1758) and neither named storage in its headline.

**What counts, measured rather than assumed.** GitHub's own documentation:
*"Actions artifacts and GitHub Packages storage share the same pooled
allowance"*, and *"Cache storage is a separate allowance of 10 GB per
repository. Cache storage is not shared with artifacts or GitHub Packages."*
Caches are therefore NOT in this number — a correction to what the cards said
while the wall was being hit, where 36 GB of caches were being counted as part
of a 2 GB pool.

**Why this measures the estate itself instead of reading a bill.** The billing
endpoint reports `Actions storage` in **GigabyteHours** — the integral of what
was held over time, not what is held now — so a month that ended under the cap
and a month that spent one day at 30× the cap can print the same number. Worse,
measured 2026-09-22: the daily breakdown and the monthly total for the same
period disagree by four orders of magnitude. A gauge built on that number cannot
say whether the next upload will fail.

**Packages are measured through the registry, and the count is honest about its
own coverage.** The packages API does not report a version's size, so each
tagged version's manifest is fetched and the **unique blob digests** are summed
(layers are shared between versions; counting them per version would report a
number several times larger than the truth). Untagged versions reached as
children of those manifests are covered; any that remain unreferenced are
reported as uncovered rather than silently dropped, because a gauge that quietly
measures less is how the first version of this script printed a confident total
while half its packages had failed to list.

⚠ **A failed listing is a failure, not a zero.** Rate limits are the ordinary
case here (a full sweep is ~300 registry calls), so requests retry with backoff
and an exhausted retry stops the run. The alternative, which happened, is a
smaller number that looks like good news.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
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
GB = 1_000_000_000


class MeasurementFailed(RuntimeError):
    """Raised instead of returning a smaller, confident-looking number."""


def _request(url: str, headers: dict[str, str], attempts: int = 4) -> Any:
    delay = 2.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as resp:
                body = resp.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (403, 429) and attempt < attempts - 1:
                wait = float(exc.headers.get("Retry-After") or delay)
                time.sleep(wait)
                delay *= 2
                continue
            raise MeasurementFailed(f"{exc.code} {url}") from exc
        except urllib.error.URLError as exc:
            if attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise MeasurementFailed(f"{exc.reason} {url}") from exc
    raise MeasurementFailed(url)


def _api(path: str, token: str) -> Any:
    return _request(
        f"{API}{path}",
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "storage-usage",
        },
    )


def _manifest(org: str, package: str, ref: str, token: str) -> Any:
    # The registry takes the SAME personal access token, base64-encoded.
    return _request(
        f"{REGISTRY}/v2/{org}/{package}/manifests/{ref}",
        {
            "Authorization": f"Bearer {base64.b64encode(token.encode()).decode()}",
            "Accept": MANIFEST_TYPES,
            "User-Agent": "storage-usage",
        },
    )


def _paged(path: str, token: str, key: str | None = None, pages: int = 30) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        joiner = "&" if "?" in path else "?"
        body = _api(f"{path}{joiner}per_page=100&page={page}", token)
        batch = body if key is None else (body or {}).get(key, [])
        if not isinstance(batch, list) or not batch:
            break
        out += batch
    return out


def artifact_bytes(org: str, token: str) -> list[tuple[int, str, int]]:
    """(bytes, repo, count) for every repository's unexpired artifacts."""
    rows = []
    for repo in _paged(f"/orgs/{org}/repos", token):
        name = repo["name"]
        arts = _paged(f"/repos/{org}/{name}/actions/artifacts", token, key="artifacts")
        live = [a for a in arts if not a.get("expired")]
        if live:
            rows.append((sum(a["size_in_bytes"] for a in live), name, len(live)))
    rows.sort(reverse=True)
    return rows


def collect_blobs(fetch, versions: list[dict[str, Any]]) -> tuple[dict[str, int], set[str]]:
    """Unique blob digest -> size, plus every version digest the walk reached.

    Pure apart from `fetch`, which takes a reference and returns a manifest (or
    None). Kept separate from the network so the counting rule -- **a layer
    shared by thirty versions is stored once** -- is testable: counting per
    version reports a number several times larger than the truth.
    """
    blobs: dict[str, int] = {}
    seen: set[str] = set()
    for version in versions:
        seen.add(version["name"])
        manifest = fetch(version["name"])
        if manifest is None:
            continue
        children = manifest.get("manifests")
        if children:
            for child in children:
                seen.add(child["digest"])
                blobs[child["digest"]] = child.get("size", 0)
                inner = fetch(child["digest"])
                if inner:
                    for blob in inner.get("layers", []) + [inner["config"]] * ("config" in inner):
                        blobs[blob["digest"]] = blob.get("size", 0)
        else:
            for blob in manifest.get("layers", []) + [manifest["config"]] * ("config" in manifest):
                blobs[blob["digest"]] = blob.get("size", 0)
    return blobs, seen


def package_bytes(org: str, token: str) -> tuple[list[tuple[int, str, int]], int, int]:
    """(bytes, package, versions) plus (covered, total) version counts.

    Only tagged versions are walked; untagged ones are covered when they are
    children of a tagged index. Any that remain unreferenced are REPORTED as
    uncovered rather than dropped in silence -- the first version of this script
    printed a confident total while half its packages had failed to list.
    """
    rows = []
    covered_total = 0
    version_total = 0
    for package in _paged(f"/orgs/{org}/packages?package_type=container", token):
        name = package["name"]
        versions = _paged(f"/orgs/{org}/packages/container/{name}/versions", token)
        version_total += len(versions)
        tagged = [v for v in versions if v.get("metadata", {}).get("container", {}).get("tags")]
        blobs, seen = collect_blobs(lambda ref: _manifest(org, name, ref, token), tagged)
        covered_total += sum(1 for v in versions if v["name"] in seen)
        if blobs:
            rows.append((sum(blobs.values()), name, len(versions)))
    rows.sort(reverse=True)
    return rows, covered_total, version_total


def render(
    artifacts: list[tuple[int, str, int]],
    packages: list[tuple[int, str, int]],
    covered: int,
    versions: int,
    warn_at: float,
) -> tuple[str, bool]:
    art_total = sum(size for size, _, _ in artifacts)
    pkg_total = sum(size for size, _, _ in packages)
    total = art_total + pkg_total
    lines = [f"paketler {pkg_total / GB:.2f} GB, artefaktlar {art_total / GB:.2f} GB"]
    for size, name, count in packages[:5]:
        lines.append(f"  paket  {name:26} {size / GB:6.2f} GB  ({count} surum)")
    for size, name, count in artifacts[:3]:
        lines.append(f"  artefakt {name:24} {size / GB:6.2f} GB  ({count} adet)")
    uncovered = versions - covered
    if uncovered:
        # Honest rather than tidy: these are stored and NOT in the number.
        lines.append(f"  ⚠ {uncovered}/{versions} surum olculemedi (etiketsiz ve sahipsiz)")
    over = total >= warn_at * GB
    verdict = "ESIK ASILDI" if over else "esik altinda"
    lines.append(f"TOPLAM {total / GB:.2f} GB / esik {warn_at:.2f} GB — {verdict}")
    return "\n".join(lines), over


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", required=True)
    ap.add_argument(
        "--warn-at", type=float, required=True, help="threshold in GB; no default on purpose"
    )
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN yok", file=sys.stderr)
        return 2

    try:
        artifacts = artifact_bytes(args.org, token)
        packages, covered, versions = package_bytes(args.org, token)
    except MeasurementFailed as exc:
        # A partial sweep prints a smaller total, which reads as good news.
        print(f"OLCUM DUSTU: {exc}", file=sys.stderr)
        return 2

    report, over = render(artifacts, packages, covered, versions, args.warn_at)
    print(report)
    # Report-only, like every other scanner here: the caller classifies.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
