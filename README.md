# narthelix/ci-scripts

Generic development tooling that this organisation's CI, git hooks and local
tasks all run. **This repository is public, and that is the point:** CI in a
private repository cannot fetch a file from a sibling private repository —
`GITHUB_TOKEN` is scoped to the repository running the workflow, and a
cross-repo read would mean a personal access token, which is one more
credential to rotate. A public repository is fetched with no credential at all.

Everything here is therefore written to be safe to publish.

## The scope lock

**Only generic tooling that reveals nothing about our infrastructure belongs
here.** Concretely, the following stay in `narthelix/.github`, which is private:

- runner labels, host names, IP addresses, network or cluster topology
- issue-tracker conventions, branch regexes, org-specific process rules
- anything reading a secret, or naming where a secret lives

A useful test before adding a file: *if a stranger reads this, do they learn
anything about how we are built, or only how a link checker works?* Only the
second kind belongs here.

This boundary erodes by accident, not by decision — one script that "just needs
the runner label" is how it goes. There is no such script; pass what it needs as
an argument instead.

## Tools

### `docs_check.py`

Mechanical documentation checks. It **detects and reports; it never rewrites**
— telling an instruction apart from the rationale for an instruction is the
judgement a scanner cannot make, and deleting the second removes the only thing
stopping someone from undoing the first.

```sh
python3 docs_check.py --root .
python3 docs_check.py --root . --ledger-path specs/technical/build_state.md \
                     --ledger-budget-bytes 24000
```

Two checks:

1. **Local link targets resolve.** A markdown link pointing at a path must point
   at something that exists. A relative path escaping the repository fails as
   such — CI checks out one repository, so it cannot be verified there; use an
   absolute URL for cross-repository references.
2. **Ledger budget** (optional). An entry-state document has a byte ceiling.
   Crossing it is a prompt to decide, not an instruction to delete, and the
   failure message says so.

Exits non-zero when it finds something, so it works as a git hook, a CI gate and
a local command without changing shape.

**Three false-positive classes it handles deliberately**, each measured on a
694-link sweep before the tool was written — a checker that gets these wrong
gets switched off, which is worse than not having one:

| Class | Why it bites | Found |
|---|---|---|
| Code spans and fenced blocks | a guide *about* linking shows `[x](../a/b.md)` as an example | 2 |
| Percent-encoded targets | `Uygulama%20Görsel%20Üretimi.md` exists; skipping `unquote` reports a live file as dead | 1 |
| Vendored trees | `vendor/bundle/ruby/**` alone produced 53 findings, none of them ours | 53 |
