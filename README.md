# aiboard

A Jira/Trello-style issue tracker where **the filesystem is the database**.
Built for AI agents, readable by humans.

- Every **task** is a folder holding `brief.md` (what to do), `worklog.md` (what was done) and, once someone talks about it, `comments.md` (the discussion).
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

## Installation

Requires Python 3.9+ and nothing else.

```bash
# recommended: an isolated install that puts `aiboard` on your PATH
pipx install git+https://github.com/flashist/aiboard.git     # or: pipx install /path/to/aiboard

# or plain pip
pip install git+https://github.com/flashist/aiboard.git      # or: pip install /path/to/aiboard

# or zero-install from a checkout
git clone https://github.com/flashist/aiboard.git ~/aiboard
ln -s ~/aiboard/bin/aiboard ~/.local/bin/aiboard          # any directory on your PATH
```

`aiboard --version` confirms it works. `python3 -m aiboard` is always available
as a fallback when running from the source tree.

## Quick start

```bash
cd my-product
aiboard init board --name "My product"   # creates board/ and a pointer file aiboard.json
aiboard agents-md --claude               # adds instructions to AGENTS.md and CLAUDE.md

aiboard sprint new "Sprint 1" --goal "Ship v1" --start 2026-09-14 --end 2026-09-25 --status in-progress
aiboard task new "Write the spec" --sprint S-001 --priority high --assignee claude
aiboard task start T-001 --as claude     # atomic claim + in-progress
aiboard task log  T-001 "Drafted section 1, open question about X" --by claude
aiboard task change-status T-001 done --note "Merged in PR #12"
aiboard sprint show S-001                # progress + task list
aiboard board                            # kanban in the terminal
aiboard serve                            # web board at http://127.0.0.1:8484
```

`aiboard init` with no directory creates the board in the current directory
instead. Either way the CLI finds the board from anywhere inside the project,
like git finds its repository: it walks up looking for `aiboard.json` (a board
or a pointer `{"board": "board"}`) or a `tasks/` + `sprints/` pair. `--root PATH`
or `AIBOARD_ROOT` override discovery.

Every command accepts `--json` for machine-readable output. With `--json`, every
failure, including bad arguments, prints `{"error": "..."}` to stdout and exits 1.
`task list` returns metadata only; `task show` adds the brief and worklog.
`AIBOARD_AUTHOR` sets the default author for worklog entries.

## Using it from AI agents

aiboard is not tied to any model or vendor. There are three ways in, from
simplest to most integrated:

**1. Instructions file.** `aiboard agents-md` inserts a marked, idempotent
block into `AGENTS.md` (read by Codex, Cursor, Copilot, Gemini CLI and
others); `--claude` also writes it into `CLAUDE.md` for Claude Code. The block
explains the folder layout and the CLI loop. Re-run it after upgrading aiboard.

**2. CLI with `--json`.** Any agent that can run shell commands can drive the
board. Set `AIBOARD_AUTHOR` to the agent's name so worklogs show who did what.

**3. MCP server.** `aiboard mcp` speaks the Model Context Protocol over stdio,
so agents call typed tools (`list_tasks`, `create_task`, `change_task_status`,
`log_work`, `get_sprint`, ...) instead of shelling out. Zero dependencies.

```bash
# Claude Code (project scope, from inside the project)
claude mcp add aiboard -- aiboard mcp --author claude

# Codex CLI: ~/.codex/config.toml
[mcp_servers.aiboard]
command = "aiboard"
args = ["mcp", "--author", "codex"]

# Cursor / Windsurf / generic: .cursor/mcp.json or equivalent
{ "mcpServers": { "aiboard": { "command": "aiboard", "args": ["mcp", "--author", "cursor"] } } }
```

The server resolves the board from its working directory (or `--root`), and
reports its name and location in the MCP `instructions` field on connect.
Verified end to end with Claude Code and Codex CLI; note that Codex asks for
approval before MCP tool calls unless its approval policy allows them.

## Commands

| Command | What it does |
| --- | --- |
| `aiboard init [DIR] [--name N]` | Create a board (status folders + `aiboard.json`); with DIR, also a pointer file in the current directory |
| `aiboard agents-md [--claude] [--print]` | Insert/refresh the agent instructions block in `AGENTS.md` (and `CLAUDE.md`) |
| `aiboard mcp [--author A]` | MCP server over stdio |
| `aiboard task new TITLE [--body ...] [--sprint S-001] [--priority low\|medium\|high] [--assignee X] [--label L]` | Create a task in `backlog` |
| `aiboard task list [--status S] [--sprint S-001] [--assignee X] [--unblocked\|--blocked\|--stale]` | List tasks |
| `aiboard task show T-001` | Brief + worklog |
| `aiboard task change-status T-001 STATUS [--note ...] [--force]` | Move the folder, append a worklog entry; refuses to start a blocked task unless forced |
| `aiboard task block T-002 T-001` / `unblock ...` | Record that T-002 is blocked by T-001 (Jira: "is blocked by"); logged in the worklog |
| `aiboard task start T-001 [--as NAME]` | Claim a backlog task and move it to in-progress in one atomic step (Jira: Start progress); fails if someone else holds it |
| `aiboard task assign T-001 NAME [--force]` | Set (or, with no name, clear) the assignee; refuses to take a task from someone else unless forced |
| `aiboard task log T-001 "text"` | Append a worklog entry (`-` reads stdin) |
| `aiboard task comment T-001 "text"` | Add to the task's discussion in `comments.md`: questions for humans, review notes, answers |
| `aiboard task edit T-001 [--title] [--priority] [--assignee] [--sprint] [--label]` | Change metadata; keeps sprint files in sync |
| `aiboard sprint new TITLE [--goal ...] [--start D] [--end D]` | Create a sprint in `backlog` |
| `aiboard sprint list` / `show S-001` / `move S-001 STATUS` | Inspect and move sprints |
| `aiboard sprint add S-001 T-001 T-002` / `remove ...` | Attach or detach tasks |
| `aiboard sprint refresh S-001` | Re-render the `## Tasks` checklist in `sprint.md` after hand edits |
| `aiboard board [--sprint S-001]` | Terminal kanban |
| `aiboard info` | Which board the CLI resolves to from here, with a summary |
| `aiboard check [--fix]` | Report inconsistencies (exit 1 if any); `--fix` repairs stale sprint checklists |
| `aiboard serve [--port 8484]` | Web board (auto-refreshes) |

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
blocked_by:            # tasks that must be done or cancelled first
  - T-003
created: "2026-09-12T19:29:51Z"
updated: "2026-09-12T19:31:02Z"
---

# Write the spec

Free-form Markdown. Acceptance criteria, links, context, whatever the
worker needs. Agents may edit this file directly.
```

A task is **blocked** while any id in `blocked_by` is still in `backlog` or
`in-progress`. `task list --unblocked` (or the MCP `list_tasks` with
`unblocked: true`) returns only work that can start now, and `change-status
... in-progress` refuses a blocked task unless `--force` is given. `check`
reports missing blockers and dependency cycles.

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

### `tasks/<status>/T-001-<slug>/comments.md`

Same entry format as the worklog, but a different purpose: the worklog is the
worker's own record, the comments are the conversation *about* the task. A
reviewer's note, an agent's question for the product owner, and the answer
all go here. The web board shows a comment count on each card, and agents
are told to check comments on their task before finishing.

### Stale tasks

A task that is in progress with no worklog entry or comment for longer than
`stale_after_hours` (default 24, set in `aiboard.json`) is **stale**: it
shows up in `aiboard check`, `task list --stale`, and as a chip on the board.
This is how an abandoned task, for example one whose agent crashed, becomes
visible.

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

Ids are allocated as *highest existing number + 1* across all status
folders, so a hand-made `T-100` makes the next CLI task `T-101`. Pick the next
free number when creating by hand. After moving folders by hand, run
`aiboard sprint refresh S-00N` (or `aiboard check --fix`) so the checklist in
`sprint.md` matches reality.

Run `aiboard check` afterwards to confirm the board is consistent. See
`AGENTS.md` for the recommended agent workflow.

## Web board

`aiboard serve` runs a small local server with no state of its own. Every
request re-reads the folders, so the page is always in sync with what agents
write, and it refreshes itself every few seconds.

For humans it is a Trello-style board: drag cards between columns to change
status, open a card to change sprint, priority or assignee, add worklog
entries, and create tasks and sprints from forms. Type your name in the header
once; it is remembered in the browser and used as the author of everything you
do. `aiboard serve --read-only` turns editing off for display-only screens.

Writes go through the same store code as the CLI and the MCP server, so the
files stay consistent no matter who edits.

API: `GET /api/board`, `GET /api/tasks/T-001`, `GET /api/sprints/S-001`,
`GET /api/check`; `POST /api/tasks`, `POST /api/tasks/T-001/{status,assign,log,edit}`,
`POST /api/sprints`, `POST /api/sprints/S-001/status`, `POST /api/check/fix`.
POST bodies are JSON and may carry an `author`.

## Development

```bash
python3 -m unittest discover -s tests -v
```

## Design decisions

- **Folder = status.** No status field to drift out of sync. `ls tasks/in-progress` is the query.
- **Zero dependencies.** Agents can shell out to `python3 -m aiboard` anywhere Python exists.
- **Stable ids with a slug suffix.** `T-001-fix-login` sorts, is unique, and is readable. References use the id only, so renaming a task does not break sprints.
- **One store, three doors.** CLI, MCP server and web board all call the same store module, so every writer keeps the files consistent the same way.
- **Safe for parallel agents.** Every write takes an advisory lock on `.aiboard.lock` in the board root, so two agents creating tasks at once never get the same id and folder moves never interleave with sprint-file rewrites. `task start` claims work atomically, so two agents can never both believe they own a task.
- **Git-friendly.** Everything is text; the whole board can live in the repo it tracks, and history comes for free.
