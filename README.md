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
- anything reading a secret, or naming where a secret lives
- anything whose value would have to be a *default* here to be useful

A useful test before adding a file: *if a stranger reads this, do they learn
anything about how we are built, or only how a link checker works?* Only the
second kind belongs here.

This boundary erodes by accident, not by decision — one script that "just needs
the runner label" is how it goes. There is no such script; pass what it needs as
an argument instead.

**Mechanisms may live here; the values they run on may not** (ADR-0099). A gate
that checks branch names is a mechanism, and it belongs here — a public
repository is the only place every repo in the org can reach, public ones
included. The runner it executes on is infrastructure, so `runner` is a
**required** input with no default: this file cannot hold the answer, and a
caller that omits it fails loudly instead of quietly moving onto billed
hosted minutes.

## Reusable workflows

`pr-conventions.yml` and `secret-scan.yml` are called by every repository in
the org. **A public repository cannot call a reusable workflow stored in a
private one** — GitHub reports `workflow was not found`, creates no jobs, and
the repository looks quiet rather than broken. That is how both public repos
here ran for weeks with no gate at all before it was noticed
(narthelix/muznara#1369). Callers pin a tag, never `main`: a moving ref on a
required check means an edit here turns every repo's PRs red at once.

```yaml
  pr-conventions:
    if: github.event_name == 'pull_request'
    permissions: { contents: read, pull-requests: read }
    uses: narthelix/ci-scripts/.github/workflows/pr-conventions.yml@v0.4.0
    with:
      runner: ubuntu-latest   # required — see the scope lock above
```

## Tools

### `docs_check.py`

Mechanical documentation checks. It **detects and reports; it never rewrites**
— telling an instruction apart from the rationale for an instruction is the
judgement a scanner cannot make, and deleting the second removes the only thing
stopping someone from undoing the first.

```sh
python3 docs_check.py --root .
```

Per-repo settings live in a `.docs-check.json` at the repository root, so the
numbers have **exactly one home** — otherwise a ledger budget ends up written in
the CI workflow *and* in the git hook, two copies free to disagree the day
someone edits one:

```json
{ "ledger_path": "specs/technical/build_state.md", "ledger_budget_bytes": 20000 }
```

Flags still override it for a one-off run (`--ledger-budget-bytes`, `--root`).

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

### `trace_check.py`

Typed + versioned traceability ([ADR-0098](https://github.com/narthelix/handbook/blob/main/adr/0098-tipli-surumlu-iz-kimligi.md)).
Connects a written rule to the code implementing it, and a decision to the
fitness function enforcing it. Both links break **silently** today.

```sh
python3 trace_check.py rules --rules-file specs/rules/business_rules.md
python3 trace_check.py tags  --code-root .
python3 trace_check.py link  --rules-file ../spec-repo/rules.md --code-root ../code-repo \
                             --adr-dir ../handbook/adr --adr-dir ../spec-repo/adr
```

**Why the id carries a revision.** Bump a rule and existing coverage becomes
`outdated`, mechanically. That is the only mechanical answer to a sentence that
stays *grammatically* true after the thing it described changed — nothing is
broken, so nothing else notices.

**Why three modes rather than one gate.** A rule can live in a spec repository
while its implementation lives in a code repository, and a CI token is scoped to
the repository running the workflow. So each CI checks the half it can see
(`rules`, `tags`) and the link itself is checked wherever both halves are on
disk (`link`).

⚠ **Two measured traps this handles**, both pinned by tests:

* **A stub can shadow the real decision.** When an ADR moves between
  directories a stub is often left under the same number; it carries no
  `**Status:**` line, so naive last-wins indexing reads the stub and a
  superseded decision looks live. Measured: 38 numbers existed in both
  directories. Files stating a status win.
* **An outdated tag is still an attempt at coverage.** Reporting "no coverage"
  on top of "your tag is outdated" tells the author something false and doubles
  the count for one mistake.

