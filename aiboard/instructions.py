"""Agent-facing instructions, shared by `aiboard agents-md` and the MCP server."""
from __future__ import annotations

import re
from pathlib import Path

BEGIN = "<!-- aiboard:begin -->"
END = "<!-- aiboard:end -->"

AGENTS_BLOCK = """## Issue tracking with aiboard

This project tracks work in **aiboard**, a file-based issue tracker in {root_hint}.
Tasks and sprints are folders; the parent folder is the status
(`backlog`, `in-progress`, `done`, `cancelled`). Each task folder holds
`brief.md` (what to do) and `worklog.md` (what was done, append-only).

Use the CLI (`aiboard`, or `python3 -m aiboard`) from anywhere inside the
project; it finds the board automatically. Add `--json` for machine-readable
output; errors are then `{{"error": "..."}}` on stdout with exit code 1.
Set `AIBOARD_AUTHOR=<your-agent-name>` so worklog entries are attributed.

Working loop:

0. First, answer people: `aiboard --json task list --assignee <you> --needs-reply` lists your tasks (any status) whose latest comment is not yours. Act on those before new work.
1. Find work: `aiboard --json task list --status backlog --unblocked [--sprint S-001]`
2. Take it: `aiboard task start T-007` (atomic claim + in-progress; if it fails, another agent got there first: pick the next task)
3. Read the brief: `aiboard --json task show T-007`
4. Log as you go: `aiboard task log T-007 "Implemented X. Next: Y."` (use `-` to pipe Markdown via stdin)
5. Need a human? `aiboard task comment T-007 "Question: ..."` and move on; answers arrive as comments (`task show`).
6. Finish: `aiboard task change-status T-007 done --note "PR #12"` (or `cancelled --note "why"`). Read the comments first; a reviewer may have left notes.
7. Verify: `aiboard check` (or `aiboard check --fix` after editing files by hand)

Create work with `aiboard task new "Title" --sprint S-001 --priority high --body-file -`;
add `--blocked-by T-003` when another task must finish first (or later: `aiboard task block T-009 T-003`).
Sprints: `aiboard sprint list`, `aiboard sprint show S-001`, `aiboard sprint new "Name" --goal "..."`.

If your harness supports MCP, `aiboard mcp` exposes the same operations as
typed tools (list_tasks, get_task, create_task, change_task_status, log_work, ...).

Rules: never write a status into `brief.md` (the folder is the status);
`worklog.md` is append-only; ids like `T-007` are permanent.
"""


def render_block(root_hint: str = "this repository") -> str:
    return f"{BEGIN}\n{AGENTS_BLOCK.format(root_hint=root_hint).rstrip()}\n{END}\n"


def install_block(path: Path, root_hint: str = "this repository") -> str:
    """Insert or replace the marked block in ``path``. Returns 'created' | 'updated' | 'unchanged'."""
    block = render_block(root_hint)
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    if pattern.search(text):
        new = pattern.sub(lambda _m: block, text, count=1)
        if new == text:
            return "unchanged"
        path.write_text(new, encoding="utf-8")
        return "updated"
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + block, encoding="utf-8")
    return "updated"
