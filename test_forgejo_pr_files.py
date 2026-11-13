#!/usr/bin/env python3
"""Tests for forgejo_pr_files.py -- run with `python3 test_forgejo_pr_files.py`.

What matters: a deleted file must come out `removed` (risk_class makes a deletion Ask), a
workflow pin bump must keep its hunk (risk_class judges it by effect), a rename is reported
under its new path, and garbage yields no files (risk_class then fails closed to Ask).
"""

from __future__ import annotations

import sys

import forgejo_pr_files as fp
import risk_class as rc

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")


DIFF = """diff --git a/README.md b/README.md
index 1111111..2222222 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-old
+new
 same
diff --git a/old.py b/old.py
deleted file mode 100644
index 3333333..0000000
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-print(1)
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+print(2)
diff --git a/a.txt b/b.txt
similarity index 100%
rename from a.txt
rename to b.txt
diff --git a/.forgejo/workflows/ci.yml b/.forgejo/workflows/ci.yml
index 5555555..6666666 100644
--- a/.forgejo/workflows/ci.yml
+++ b/.forgejo/workflows/ci.yml
@@ -10,1 +10,1 @@
-      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v4
+      - uses: actions/checkout@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v5
"""

files = fp.split(DIFF)
check("count", len(files), 5)
check("modified", (files[0]["status"], files[0]["filename"]), ("modified", "README.md"))
check("removed", (files[1]["status"], files[1]["filename"]), ("removed", "old.py"))
check("added", (files[2]["status"], files[2]["filename"]), ("added", "src/new.py"))
check("renamed", (files[3]["status"], files[3]["filename"], files[3]["patch"]), ("renamed", "b.txt", None))
check("hunk kept", files[0]["patch"].startswith("@@ -1,2 +1,2 @@"), True)
check("garbage", fp.split("not a diff\n"), [])

# End to end through risk_class: the deletion alone makes it Ask.
cls, _ = rc.classify([(f["status"], f["filename"], f["patch"]) for f in files])
check("deletion -> ask", cls, "ask")
# A docs-only diff stays Ship.
cls, _ = rc.classify([(f["status"], f["filename"], f["patch"]) for f in files[:1]])
check("docs -> ship", cls, "ship")
# Nothing parsed -> fail closed.
cls, _ = rc.classify([])
check("empty -> ask", cls, "ask")

print("ok" if FAILS == 0 else f"{FAILS} hata")
sys.exit(1 if FAILS else 0)
