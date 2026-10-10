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
check("migration → ask", cls([(m, "src/x.py"), ("added", "migrations/versions/0042_add.py")]), "ask")
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
      "database migration: migrations/v/1.py" in rc.classify([(m, "migrations/v/1.py")])[1], True)

print(f"\n{'FAIL' if FAILS else 'ok'}: {FAILS} başarısız")
sys.exit(1 if FAILS else 0)
