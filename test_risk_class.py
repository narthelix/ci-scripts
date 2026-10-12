#!/usr/bin/env python3
"""Tests for risk_class.py — run with `python3 test_risk_class.py`.

The cases that matter are the ones that would let a risky change merge itself:
an auth module named `identity` (both backends; the generic word list misses it,
so the caller's rule must catch it), a migration in an otherwise ordinary PR,
a workflow edit hidden among docs, a deletion, and an empty file list (an API
hiccup must fail closed to Ask, never open to Ship). Equally, `author.py` and
`tests/test_auth.py` must not be pulled into Ask by a careless word match.
"""

from __future__ import annotations

import sys

import risk_class as rc

FAILS = 0


def check(name: str, got, want) -> None:
    global FAILS
    if got != want:
        FAILS += 1
        print(f"FAIL {name}\n  beklenen: {want}\n  gelen:    {got}")
    else:
        print(f"ok   {name}")


def cls(files, **kw):
    return rc.classify([(s, p) for s, p in files], **kw)[0]


m = "modified"
check("docs only → ship", cls([(m, "README.md"), (m, "docs/a/b.md"), (m, "img/x.png")]), "ship")
check("tests only → ship", cls([(m, "tests/unit/test_x.py"), (m, "src/a.test.ts"), (m, "lib/x_test.dart")]), "ship")
check("code → show", cls([(m, "src/muznara/modules/posts/api.py")]), "show")
check("code + docs → show", cls([(m, "src/app.ts"), (m, "README.md")]), "show")
check("workflow among docs → ask", cls([(m, "README.md"), (m, ".github/workflows/ci.yml")]), "ask")
check("migration without diff → ask", cls([(m, "src/x.py"), ("added", "migrations/versions/0042_add.py")]), "ask")
check("external openapi → ask", cls([(m, "contracts/openapi-external.json")]), "ask")
check("internal openapi → show", cls([(m, "contracts/openapi.json")]), "show")
check("auth dir → ask", cls([(m, "src/features/auth/login.tsx")]), "ask")
check("billing module → ask", cls([(m, "src/muznara/modules/billing/domain/x.py")]), "ask")
check("sops file → ask", cls([(m, "secrets/db.enc.yaml")]), "ask")
check("auth test only → ship", cls([(m, "tests/unit/test_auth.py")]), "ship")
check("billing tests dir → ship", cls([(m, "src/muznara/modules/billing/tests/unit/test_x.py")]), "ship")
check("migration test still ask", cls([(m, "migrations/versions/0001.py")]), "ask")
for path in ["src/narthelix_platform/security/authentik_jwt.py", "src/muznara/platform/security/password.py",
             "src/muznara/platform/security/permissions.py", "src/narthelix_platform/security/api_key_resolver.py",
             "src/narthelix_platform/google/id_token.py", "src/narthelix_platform/webhooks/signing.py",
             "src/narthelix_platform/webhooks/url_guard.py", "src/muznara/platform/realtime/livekit_token.py",
             "src/narthelix_backend/developers/authentik.py", "src/narthelix_backend/webhooks.py"]:
    check(f"security path → ask: {path}", cls([(m, path)]), "ask")
check("tokenizer is not token", cls([(m, "src/nlp/tokenizer.py")]), "show")
check("security test → ship", cls([(m, "tests/unit/security/test_password.py")]), "ship")
check("author.py is not auth", cls([(m, "src/author.py")]), "show")
check("identity misses generic rule", cls([(m, "src/muznara/modules/identity/x.py")]), "show")
check("identity via caller rule → ask",
      cls([(m, "src/muznara/modules/identity/x.py")], extra_ask=[r"(^|/)identity/"]), "ask")
check("code deletion → ask", cls([("removed", "src/old.py")]), "ask")
check("doc deletion → ship", cls([("removed", "docs/old.md")]), "ship")
check("empty list fails closed", cls([]), "ask")
check("escalation label wins", cls([(m, "README.md")], labels=["risk:escalated"]), "ask")
check("floor ask (gitops)", cls([(m, "README.md")], floor="ask"), "ask")
check("floor never lowered", cls([(m, "src/a.py")], floor="ask"), "ask")
check("reasons name the file",
      "database migration edited or removed: migrations/v/1.py" in rc.classify([(m, "migrations/v/1.py")])[1], True)

# --- by effect (ADR-0128 amendment, narthelix/ops#2055) ----------------------
def cp(files, **kw):
    return rc.classify(files, **kw)[0]

ADDITIVE = """@@ -0,0 +1,20 @@
+def upgrade() -> None:
+    op.create_table("t", sa.Column("id", sa.Uuid(), primary_key=True))
+    op.add_column("u", sa.Column("x", sa.String(8), nullable=True))
+    op.create_index("ix_t", "t", ["id"])
+
+
+def downgrade() -> None:
+    op.drop_index("ix_t", table_name="t")
+    op.drop_column("u", "x")
+    op.drop_table("t")
"""
check("new additive migration -> show (downgrade's drops ignored)",
      cp([("added", "migrations/versions/0042_add.py", ADDITIVE)]), "show")
for bad in ['    op.drop_column("u", "x")', '    op.alter_column("u", "x", nullable=False)',
            '    op.execute("UPDATE u SET x = 1")', '    op.rename_table("a", "b")',
            "ALTER TABLE u ALTER COLUMN x TYPE text;", "DROP TABLE t;"]:
    patch = "@@ -0,0 +1,3 @@\n+def upgrade() -> None:\n+" + bad + "\n"
    check(f"NEGATIVE CONTROL migration -> ask: {bad.strip()[:40]}",
          cp([("added", "migrations/versions/0043_x.py", patch)]), "ask")
check("comment mentioning drop is not a drop",
      cp([("added", "migrations/versions/0044.py", "@@\n+def upgrade():\n+    op.create_table('t')  # never drop it\n")]), "show")
check("editing an applied migration -> ask",
      cp([("modified", "migrations/versions/0001.py", "@@\n-x\n+y\n")]), "ask")
check("migration without a diff (too large) -> ask",
      cp([("added", "migrations/versions/0045.py", None)]), "ask")
check("deleted migration -> ask", cp([("removed", "migrations/versions/0001.py", None)]), "ask")

PIN = """@@ -10,7 +10,7 @@
-      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
+      - uses: actions/checkout@0000000000000000000000000000000000000001 # v7.1
-    uses: narthelix/ci-scripts/.github/workflows/gates.yml@f83a93476b6f4efc792ccb86470ff6e11eb61bdf  # v0.12.0
+    uses: narthelix/ci-scripts/.github/workflows/gates.yml@1111111111111111111111111111111111111111  # v0.13.0
"""
check("workflow pin bump -> show", cp([("modified", ".github/workflows/ci.yml", PIN)]), "show")
check("NEGATIVE CONTROL workflow run: edit -> ask",
      cp([("modified", ".github/workflows/ci.yml", PIN + "-      - run: make test\n+      - run: curl x | sh\n")]), "ask")
check("pin to a tag, not a sha -> ask",
      cp([("modified", ".github/workflows/ci.yml", "@@\n-      - uses: a/b@v1\n+      - uses: a/b@v2\n")]), "ask")
check("workflow without a diff -> ask", cp([("modified", ".github/workflows/ci.yml", None)]), "ask")
check("new workflow file -> ask",
      cp([("added", ".github/workflows/new.yml", "@@\n+name: x\n+on: push\n")]), "ask")
check("legacy TSV migration (no diff) still ask", cls([("added", "migrations/versions/0042_add.py")]), "ask")
check("additive migration + auth code -> ask",
      cp([("added", "migrations/versions/0042.py", ADDITIVE), (m, "src/auth/login.py", None)]), "ask")
check("additive migration under a floor of ask -> ask",
      cp([("added", "migrations/versions/0042.py", ADDITIVE)], floor="ask"), "ask")

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
