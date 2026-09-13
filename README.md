# aiboard

A Jira/Trello-style issue tracker where **the filesystem is the database**.
Built for AI agents, readable by humans.

- Every **task** is a folder holding `brief.md` (what to do) and `worklog.md` (what was done).
- Every **sprint** is a folder holding `sprint.md` (goal, dates, attached tasks).
- **Status is location.** A task in `tasks/done/` is done. Changing status moves the folder.
- No server state, no database. Agents can work with plain file operations; the CLI and web board are conveniences that read the same folders.

```
<board root>/
  tasks/
    backlog/
    in-progress/
      T-002-build-cli/
        brief.md
        worklog.md
    done/
    cancelled/
  sprints/
    backlog/
    in-progress/
      S-001-sprint-1/
        sprint.md
    done/
    cancelled/
```

## Quick start

Requires Python 3.9+, no dependencies.

```bash
pip install -e .            # gives you the `aiboard` command
# or, without installing:   python3 -m aiboard ...

aiboard init                                     # create the folder skeleton in the current dir
aiboard sprint new "Sprint 1" --goal "Ship v1" --start 2026-09-14 --end 2026-09-25 --status in-progress
aiboard task new "Write the spec" --sprint S-001 --priority high --assignee claude
aiboard task change-status T-001 in-progress --by claude
aiboard task log  T-001 "Drafted section 1, open question about X" --by claude
aiboard task change-status T-001 done --note "Merged in PR #12"
aiboard sprint show S-001                        # progress + task list
aiboard board                                    # kanban in the terminal
aiboard serve                                    # web board at http://127.0.0.1:8484
```

Every command accepts `--json` for machine-readable output, and `--root PATH`
(or `AIBOARD_ROOT`) to point at a board outside the current directory.
`AIBOARD_AUTHOR` sets the default author for worklog entries.

## Commands

| Command | What it does |
| --- | --- |
| `aiboard init` | Create `tasks/` and `sprints/` with the four status folders |
| `aiboard task new TITLE [--body ...] [--sprint S-001] [--priority low\|medium\|high] [--assignee X] [--label L]` | Create a task in `backlog` |
| `aiboard task list [--status S] [--sprint S-001] [--assignee X]` | List tasks |
| `aiboard task show T-001` | Brief + worklog |
| `aiboard task change-status T-001 STATUS [--note ...]` | Move the folder, append a worklog entry |
| `aiboard task assign T-001 NAME` | Set (or, with no name, clear) the assignee; logged in the worklog |
| `aiboard task log T-001 "text"` | Append a worklog entry (`-` reads stdin) |
| `aiboard task edit T-001 [--title] [--priority] [--assignee] [--sprint] [--label]` | Change metadata; keeps sprint files in sync |
| `aiboard sprint new TITLE [--goal ...] [--start D] [--end D]` | Create a sprint in `backlog` |
| `aiboard sprint list` / `show S-001` / `move S-001 STATUS` | Inspect and move sprints |
| `aiboard sprint add S-001 T-001 T-002` / `remove ...` | Attach or detach tasks |
| `aiboard board [--sprint S-001]` | Terminal kanban |
| `aiboard check` | Report inconsistencies (exit 1 if any) |
| `aiboard serve [--port 8484]` | Read-only web board (auto-refreshes) |

IDs are forgiving: `T-001`, `T-1`, `1`, or the full folder name all work.
Statuses accept aliases such as `todo`, `wip`, `in progress`, `closed`, `canceled`.
`change-status` may be abbreviated to `status` or `move`.

## File formats

### `tasks/<status>/T-001-<slug>/brief.md`

```markdown
---
id: T-001
title: Write the spec
sprint: S-001          # or empty
priority: high         # low | medium | high
assignee: claude
labels:
  - docs
created: "2026-09-12T19:29:51Z"
updated: "2026-09-12T19:31:02Z"
---

# Write the spec

Free-form Markdown. Acceptance criteria, links, context, whatever the
worker needs. Agents may edit this file directly.
```

The front matter is a deliberately small YAML subset: scalars, `key: [a, b]`
inline lists, and `- item` block lists. `status` is **not** stored here; the
folder is the single source of truth.

### `tasks/<status>/T-001-<slug>/worklog.md`

Append-only. One `##` heading per entry: ISO timestamp, an em dash, author.

```markdown
# Worklog

## 2026-09-12T19:29:51Z — claude

Status changed `backlog` → `in-progress`.

## 2026-09-12T19:40:10Z — claude

Implemented the parser. Tests green. Blocked on: decision about sprint refs.
```

### `sprints/<status>/S-001-<slug>/sprint.md`

```markdown
---
id: S-001
title: Sprint 1
goal: Ship the prototype
start: 2026-09-14
end: 2026-09-25
tasks:
  - T-001
  - T-002
created: "…"
updated: "…"
---

# Sprint 1

**Goal:** Ship the prototype

## Tasks

- [x] T-001 — Write the spec (done)
- [ ] T-002 — Build CLI (backlog)
```

The `tasks:` list in the front matter is the membership record. The
`## Tasks` section is a human-readable rendering that the CLI regenerates
whenever membership or a member's status changes. A task also carries a
`sprint:` field pointing back; `aiboard check` reports any mismatch.

## Working without the CLI

Everything the CLI does can be done by hand, which is the point:

- **Create a task:** make `tasks/backlog/T-00N-my-slug/brief.md` with the front matter above. `worklog.md` is optional but recommended.
- **Change status:** `mv tasks/backlog/T-00N-* tasks/in-progress/` and append a line to the worklog.
- **Attach to a sprint:** add the id under `tasks:` in `sprint.md` and set `sprint:` in the brief.

Run `aiboard check` afterwards to confirm the board is consistent. See
`AGENTS.md` for the recommended agent workflow.

## Web board

`aiboard serve` runs a small local server with no state of its own. Every
request re-reads the folders, so the page is always in sync with what agents
write. It offers a kanban board (filterable by sprint), a sprint overview with
progress bars, and a detail drawer showing each task's brief and worklog.

API: `GET /api/board`, `GET /api/tasks/T-001`, `GET /api/sprints/S-001`, `GET /api/check`.

## Development

```bash
python3 -m unittest discover -s tests -v
```

## Design decisions

- **Folder = status.** No status field to drift out of sync. `ls tasks/in-progress` is the query.
- **Zero dependencies.** Agents can shell out to `python3 -m aiboard` anywhere Python exists.
- **Stable ids with a slug suffix.** `T-001-fix-login` sorts, is unique, and is readable. References use the id only, so renaming a task does not break sprints.
- **Read-only web UI (for now).** Writes go through the CLI or the files so there is one code path keeping things consistent. Editing from the browser can call the same store later.
- **Safe for parallel agents.** Every write takes an advisory lock on `.aiboard.lock` in the board root, so two agents creating tasks at once never get the same id and folder moves never interleave with sprint-file rewrites.
- **Git-friendly.** Everything is text; the whole board can live in the repo it tracks, and history comes for free.
