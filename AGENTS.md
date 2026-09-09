# AGENTS.md

Instructions for any AI agent working in this repository. Claude Code reads
`CLAUDE.md`; every other tool — Copilot, Cursor, Codex — reads this file.

**It deliberately copies no rules.** Two copies of a rule are two things to
correct, and the day they disagree nothing errors. Everything below is a
pointer to where the rule actually lives.

## Read these first, in this order

1. **`CLAUDE.md` in this repo** (if present). The name says Claude; the content
   is tool-agnostic. Treat it as addressed to you.
2. **`CONVENTIONS.md` in `narthelix/handbook`** — the org's working rules. Its
   own first line: *"a human, Claude Code, or any other agent reading this file
   sees the same rules."*
3. **`specs/technical/build_state.md` in `narthelix/muznara`** — the ledger:
   where the work actually stands. Read it before choosing what to do next. If
   it and anything else disagree, the ledger wins.

Those last two are **other repositories**. `narthelix/workspace` clones every
org repo side by side, so from inside one of them they are `../handbook/` and
`../muznara/`. Opening a single repo as your editor's folder puts them outside
it — read them from the terminal, or open the workspace directory instead so
that one session sees all of them.

## The shared memory

Distilled facts carried between sessions live in `narthelix/agent-memory`: one
file per fact, `MEMORY.md` the index. It is **not inside this repo and not
inside the workspace** — it is a clone under the user's home directory, at the
workspace path with `/` replaced by `-`:

```sh
~/.claude/projects/-Users-<user>-Projects-narthelix/memory
```

Editors scope file access to the open folder, so reach it from the **terminal**:

```sh
MEM=~/.claude/projects/$(cd /path/to/narthelix && pwd | tr / -)/memory
cat "$MEM/MEMORY.md"          # the index — one line per fact
cat "$MEM/<name>.md"          # the fact itself
```

Three things about it that are not obvious:

- **`MEMORY.md` is an index, not the memory.** Each line is a hook. Open the
  file it points at before acting on that topic — the *why* and the trap are in
  the file, never in the one-liner.
- **A new or corrected fact is written, committed and pushed.** Same shape as
  the existing files: frontmatter with `name` / `description` /
  `metadata.type` (`user` | `feedback` | `project` | `reference`), the fact in
  the body, `[[other-name]]` to link. Add one line to `MEMORY.md`. Then
  `git commit -m 'chore(memory): …'` and push — straight to `main`, that repo
  runs `secret-scan` and no `pr-conventions`. **Memory written only locally is
  memory one machine has**, which is the failure this repo exists to prevent.
- **It is distilled fact, not narrative.** Progress belongs in the ledger.

## Handing work back

Sessions here alternate between tools. What carries across is not chat history —
it is the ledger, the memory, and git. So, as you go rather than at the end:

- **Update `build_state.md`** when something lands. An agent picking up next
  week reads it, not your transcript.
- **Push memory facts** as above.
- **One PR per unit of work**, so the history is legible without you.

## Two gates that fail while looking correct

- **Branch names are regex-gated** (`narthelix/.github`'s `pr-conventions.yml`,
  Convention #15): `<type>/<issue-no>-<kebab>`, type one of
  `feature|fix|chore|docs|refactor`. An uppercase letter, `_`, `.`, a double or
  trailing hyphen, or a Turkish diacritic (ş/ğ/ı/ö/ü/ç) fails the required
  check while reading correctly. `mani run install-hooks` in the workspace
  rejects a bad name before the push instead of after the red PR.
- **Issues for every repo live in `narthelix/muznara`**, not the repo you are
  in. The issue number rides in the **branch name**; a bare `#N` in the PR body
  would resolve against the wrong tracker, so most repos do not require one.
