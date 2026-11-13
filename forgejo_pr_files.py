#!/usr/bin/env python3
"""A Forgejo pull request's unified diff -> the file list risk_class.py reads.

Forgejo's `pulls/{n}/files` has no `patch` (measured, Forgejo 15: additions, changes,
contents_url, deletions, filename, html_url, raw_url, status), and risk_class.py needs the
patch to judge CI pin bumps and additive migrations by effect. So the caller fetches
`pulls/{n}.diff` and this splits it per file into GitHub's shape, one JSON object a line:
{"status": added|removed|modified|renamed, "filename": <new path>, "patch": <hunks or null>}.

Fail closed is risk_class.py's job: an empty list there is Ask. Here a diff that cannot be
parsed into any file prints nothing -- and so becomes that empty list.

ADR-0153 G2f-2, narthelix/ops#2315.
"""

from __future__ import annotations

import json
import re
import sys

HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def split(diff: str) -> list[dict[str, str | None]]:
    files: list[dict[str, str | None]] = []
    cur: dict | None = None
    hunks: list[str] = []

    def close() -> None:
        if cur is not None:
            cur["patch"] = "\n".join(hunks) if hunks else None
            files.append(cur)

    for line in diff.splitlines():
        m = HEADER.match(line)
        if m:
            close()
            cur, hunks = {"status": "modified", "filename": m.group(2), "patch": None}, []
            continue
        if cur is None:
            continue
        if hunks:
            hunks.append(line)
        elif line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "removed"
        elif line.startswith("rename to "):
            cur["status"], cur["filename"] = "renamed", line[len("rename to ") :]
        elif line.startswith("@@"):
            hunks.append(line)
    close()
    return files


def main() -> int:
    for f in split(sys.stdin.read()):
        print(json.dumps(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
