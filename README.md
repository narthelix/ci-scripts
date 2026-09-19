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
(narthelix/muznara#1369). Callers pin a **full commit SHA**, never `main` and never a tag: a tag can be
moved, a commit cannot, and a moving ref on a required check means an edit here
turns every repo's PRs red at once. The version stays legible in a comment.

```yaml
  pr-conventions:
    if: github.event_name == 'pull_request'
    permissions: { contents: read, pull-requests: read }
    uses: narthelix/ci-scripts/.github/workflows/pr-conventions.yml@4c956655087857d20256c3f42bd6f2fa010d9877  # v0.4.0
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

### `lychee.toml`

The other half of the same gate: `docs_check.py` validates that a link's **file**
exists, and this configures [lychee](https://github.com/lycheeverse/lychee) to
validate its **fragment**. A heading rename otherwise breaks every deep link
into that document while nothing renders differently.

```sh
lychee --config lychee.toml './**/*.md'
```

The pinned version lives in `LYCHEE_VERSION` — one home, read by both the CI
workflow and the local task, because a version written in two places is two
versions the day one of them is bumped.

**Why lychee and not markdownlint MD051**, which narthelix/muznara#1363 was
groomed to adopt: measuring both before writing either reversed the decision.

| | markdownlint MD051 | lychee |
|---|---|---|
| `#same-file` fragment | ✅ | ✅ |
| `other.md#fragment` | ❌ **not checked** | ✅ |
| explicit `<a id="…">` anchors | ✅ | ✅ |
| GitHub slugging (em-dash → double hyphen) | ✅ | ✅ |
| code spans and fenced blocks ignored | ✅ | ✅ |
| runtime | Node | static binary |

The second row is the whole card: the class it was opened for — a ledger's deep
links into its own snapshot — is cross-file, and MD051 passes those silently.

⚠ **`exclude_path` entries are regular expressions, not directory names.** The
first draft listed bare names and silently dropped ten files from the gate,
`build_state.md` among them (`build` is a substring of it; `.git` is a regex
matching `-git` in every `flux-gitops` filename). The count went 252 → 242 and
the run stayed green. `test_docs_check.py` pins both directions: every
`SKIP_DIRS` directory is excluded, and the real documents that vanished are not.

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


### `dead_jobs.py`

Finds CI jobs that are red and **stop nobody** — the failures nothing reports.

```sh
GITHUB_TOKEN=... python3 dead_jobs.py --org <org> --window 10
GITHUB_TOKEN=... python3 dead_jobs.py --org <org> --format json
```

A red pull-request check blocks a merge, so it announces itself. A red
scheduled or event-driven run blocks nothing: the work simply does not happen
and the board looks the same either way. Measured 2026-09-19 across one
organisation's 77 active workflows — **five were dead**, one for three weeks,
one that had never been green in its life; two of them had been found by
accident weeks earlier and nobody had asked how many more there were.

⚠ **The threshold is *no green run in the window*, never *the last run
failed*** — and that is the whole design. On the day of the measurement four
repositories had a single red `main` run because CI runners were saturated by
one batch of pushes; every one was green the day before and green again after.
Naming those would have produced four notifications nobody needs, from one
event, which is the disease this tool exists to cure wearing a different hat.
A failure that heals itself is invisible here **by choice**.

⚠ **A `workflow_call`-only file is not a dead workflow.** It has no runs of its
own — its runs belong to the repositories that call it. The first version of
this scan counted seven of them as "never ran": seven false alarms out of eight
findings. `on:` is read before any zero-run workflow is reported.

⚠ **Two traps in the API itself**, both pinned by tests: `conclusion` is an
empty string (not `null`) while a run is in flight, so any `or`-style default
scores "still running" as a verdict; and `total_count` on a filtered
`runs?status=` query reports the **unfiltered** total, so it is never used for
counting.

Exit code is 0 even with findings. The report is the output — a non-zero exit
would make this a gate that blocks something, which is exactly what it is not.
Organisation, window and exclusions are arguments with **no defaults**: where it
runs and what it watches are not facts a public repository may hold.
