<!-- aiboard:begin -->
## Issue tracking with aiboard

This project tracks work in **aiboard**, a file-based issue tracker in this repository.
Tasks and sprints are folders; the parent folder is the status
(`backlog`, `in-progress`, `done`, `cancelled`). Each task folder holds
`brief.md` (what to do) and `worklog.md` (what was done, append-only).

Use the CLI (`aiboard`, or `python3 -m aiboard`) from anywhere inside the
project; it finds the board automatically. Add `--json` for machine-readable
output; errors are then `{"error": "..."}` on stdout with exit code 1.
Set `AIBOARD_AUTHOR=<your-agent-name>` so worklog entries are attributed.

Working loop:

1. Find work: `aiboard --json task list --status backlog --unblocked [--sprint S-001]`
2. Take it: `aiboard task assign T-007 <you>` then `aiboard task change-status T-007 in-progress`
3. Read the brief: `aiboard --json task show T-007`
4. Log as you go: `aiboard task log T-007 "Implemented X. Next: Y."` (use `-` to pipe Markdown via stdin)
5. Finish: `aiboard task change-status T-007 done --note "PR #12"` (or `cancelled --note "why"`)
6. Verify: `aiboard check` (or `aiboard check --fix` after editing files by hand)

Create work with `aiboard task new "Title" --sprint S-001 --priority high --body-file -`;
add `--blocked-by T-003` when another task must finish first (or later: `aiboard task block T-009 T-003`).
Sprints: `aiboard sprint list`, `aiboard sprint show S-001`, `aiboard sprint new "Name" --goal "..."`.

If your harness supports MCP, `aiboard mcp` exposes the same operations as
typed tools (list_tasks, get_task, create_task, change_task_status, log_work, ...).

Rules: never write a status into `brief.md` (the folder is the status);
`worklog.md` is append-only; ids like `T-007` are permanent.
<!-- aiboard:end -->
