"""Command-line interface. Every command supports ``--json`` for agents."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .model import PRIORITIES, STATUSES
from .store import Board, BoardError

DEFAULT_AUTHOR = os.environ.get("AIBOARD_AUTHOR") or os.environ.get("USER") or "agent"


class Parser(argparse.ArgumentParser):
    """argparse errors honour --json too, so agents have one place to look for errors."""

    def error(self, message):
        if "--json" in sys.argv[1:]:
            print(json.dumps({"error": message}))
            sys.exit(1)
        super().error(message)


def _board(args: argparse.Namespace) -> Board:
    return Board.locate(args.root)


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(text)


def _read_body(args: argparse.Namespace) -> str:
    if getattr(args, "body_file", None):
        if args.body_file == "-":
            return sys.stdin.read()
        return Path(args.body_file).read_text(encoding="utf-8")
    return getattr(args, "body", None) or ""


# ----------------------------------------------------------------- formatting

def _task_row(t) -> str:
    sprint = t.sprint or "-"
    who = t.assignee or "-"
    return f"{t.id:<6} {t.status:<12} {t.priority:<7} {sprint:<6} {who:<12} {t.title}"


def _task_table(tasks) -> str:
    if not tasks:
        return "(no tasks)"
    header = f"{'ID':<6} {'STATUS':<12} {'PRIO':<7} {'SPRINT':<6} {'ASSIGNEE':<12} TITLE"
    return "\n".join([header] + [_task_row(t) for t in tasks])


def _sprint_table(board: Board, sprints) -> str:
    if not sprints:
        return "(no sprints)"
    rows = [f"{'ID':<6} {'STATUS':<12} {'DONE':<9} {'DATES':<23} TITLE"]
    for s in sprints:
        p = board.sprint_progress(s)
        done = f"{p['counts']['done']}/{p['total']}"
        dates = f"{s.meta.get('start') or '?'} → {s.meta.get('end') or '?'}"
        rows.append(f"{s.id:<6} {s.status:<12} {done:<9} {dates:<23} {s.title}")
    return "\n".join(rows)


# ------------------------------------------------------------------ commands

def cmd_init(args):
    board = Board(Path(args.root or args.dir or "."))
    created = board.init(name=args.name)
    _emit(args, {"root": str(board.root), "name": board.name, "created": [str(p) for p in created]},
          f"Board '{board.name}' ready at {board.root} ({len(created)} entries created)\n"
          f"Next: `aiboard agents-md` to add agent instructions, `aiboard serve` for the web board.")


def cmd_task_new(args):
    board = _board(args)
    t = board.create_task(
        title=args.title, body=_read_body(args), status=args.status, sprint=args.sprint,
        priority=args.priority, assignee=args.assignee, labels=args.label or [], author=args.by,
    )
    _emit(args, t.to_dict(), f"Created {t.id} in {t.status}: {t.path}")


def cmd_task_list(args):
    board = _board(args)
    tasks = board.list_tasks(status=args.status, sprint=args.sprint)
    if args.assignee:
        tasks = [t for t in tasks if t.assignee == args.assignee]
    _emit(args, [t.to_dict() for t in tasks], _task_table(tasks))


def cmd_task_show(args):
    board = _board(args)
    t = board.get_task(args.id)
    lines = [
        f"{t.id}: {t.title}",
        f"status:   {t.status}",
        f"sprint:   {t.sprint or '-'}",
        f"priority: {t.priority}",
        f"assignee: {t.assignee or '-'}",
        f"labels:   {', '.join(t.meta.get('labels') or []) or '-'}",
        f"folder:   {t.path}",
        "",
        t.body.strip(),
    ]
    if t.worklog:
        lines += ["", "--- worklog ---"]
        for e in t.worklog:
            lines.append(f"[{e.timestamp}] {e.author}: {e.text}")
    _emit(args, t.to_dict(include_body=True), "\n".join(lines))


def cmd_task_change_status(args):
    board = _board(args)
    t = board.move_task(args.id, args.status, author=args.by, note=args.note)
    _emit(args, t.to_dict(), f"{t.id} is now {t.status}: {t.path}")


def cmd_task_assign(args):
    board = _board(args)
    t = board.assign(args.id, args.assignee or None, author=args.by)
    _emit(args, t.to_dict(), f"{t.id} assigned to {t.assignee or 'nobody'}")


def cmd_task_log(args):
    board = _board(args)
    message = args.message if args.message != "-" else sys.stdin.read()
    t = board.log(args.id, message, author=args.by)
    _emit(args, t.to_dict(include_body=True), f"Logged to {t.id} ({len(t.worklog)} entries)")


def cmd_task_edit(args):
    board = _board(args)
    fields = {}
    if args.title:
        fields["title"] = args.title
    if args.priority:
        fields["priority"] = args.priority
    if args.assignee is not None:
        fields["assignee"] = args.assignee or None
    if args.sprint is not None:
        fields["sprint"] = args.sprint or None
    if args.label is not None:
        fields["labels"] = args.label
    if not fields:
        raise BoardError("nothing to change")
    t = board.update_task(args.id, **fields)
    _emit(args, t.to_dict(), f"Updated {t.id}")


def cmd_sprint_new(args):
    board = _board(args)
    s = board.create_sprint(title=args.title, goal=args.goal or "", body=_read_body(args),
                            status=args.status, start=args.start, end=args.end)
    _emit(args, s.to_dict(), f"Created {s.id} in {s.status}: {s.path}")


def cmd_sprint_list(args):
    board = _board(args)
    sprints = board.list_sprints(status=args.status)
    data = []
    for s in sprints:
        d = s.to_dict()
        d["progress"] = {k: v for k, v in board.sprint_progress(s).items() if k != "tasks"}
        data.append(d)
    _emit(args, data, _sprint_table(board, sprints))


def cmd_sprint_show(args):
    board = _board(args)
    s = board.get_sprint(args.id)
    p = board.sprint_progress(s)
    c = p["counts"]
    lines = [
        f"{s.id}: {s.title}",
        f"status:   {s.status}",
        f"goal:     {s.meta.get('goal') or '-'}",
        f"dates:    {s.meta.get('start') or '?'} → {s.meta.get('end') or '?'}",
        f"progress: {p['percent']}% done  "
        f"(backlog {c['backlog']}, in-progress {c['in-progress']}, done {c['done']}, cancelled {c['cancelled']})",
        f"folder:   {s.path}",
        "",
        _task_table(p["tasks"]),
    ]
    d = s.to_dict(include_body=True)
    d["progress"] = {k: v for k, v in p.items() if k != "tasks"}
    d["tasks_detail"] = [t.to_dict() for t in p["tasks"]]
    _emit(args, d, "\n".join(lines))


def cmd_sprint_change_status(args):
    board = _board(args)
    s = board.move_sprint(args.id, args.status)
    _emit(args, s.to_dict(), f"{s.id} is now {s.status}: {s.path}")


def cmd_sprint_refresh(args):
    board = _board(args)
    s = board.refresh_sprint(args.id)
    _emit(args, s.to_dict(), f"Re-rendered task list in {s.path / 'sprint.md'}")


def cmd_sprint_add(args):
    board = _board(args)
    s = board.sprint_add(args.id, args.tasks)
    _emit(args, s.to_dict(), f"{s.id} now has {len(s.tasks)} tasks: {', '.join(s.tasks)}")


def cmd_sprint_remove(args):
    board = _board(args)
    s = board.sprint_remove(args.id, args.tasks)
    _emit(args, s.to_dict(), f"{s.id} now has {len(s.tasks)} tasks: {', '.join(s.tasks) or '-'}")


def cmd_board(args):
    board = _board(args)
    snap = board.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    tasks = board.list_tasks(sprint=args.sprint)
    columns = {s: [t for t in tasks if t.status == s] for s in STATUSES}
    width = 28
    header = "".join(f"{s.upper() + ' (' + str(len(columns[s])) + ')':<{width}}" for s in STATUSES)
    print(header)
    print("-" * (width * len(STATUSES)))
    height = max((len(v) for v in columns.values()), default=0)
    for i in range(height):
        row = ""
        for s in STATUSES:
            cell = ""
            if i < len(columns[s]):
                t = columns[s][i]
                cell = f"{t.id} {t.title}"
                if len(cell) > width - 2:
                    cell = cell[: width - 3] + "…"
            row += f"{cell:<{width}}"
        print(row.rstrip())
    if not height:
        print("(no tasks)")


def cmd_check(args):
    board = _board(args)
    problems = board.check(fix=args.fix)
    remaining = [p for p in problems if not p.endswith("(fixed)")]
    _emit(args, {"ok": not remaining, "problems": problems},
          "OK: board is consistent" if not problems else "\n".join(f"- {p}" for p in problems))
    if remaining:
        sys.exit(1)


def cmd_serve(args):
    from .server import serve

    board = _board(args)
    serve(board, host=args.host, port=args.port, open_browser=not args.no_open)


# ------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="aiboard", description="File-based issue tracker for AI agents and humans.")
    p.add_argument("--root", help="board root (default: $AIBOARD_ROOT, else the nearest parent holding a board)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"aiboard {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    s = sub.add_parser("init", help="create a board (folder skeleton + aiboard.json)")
    s.add_argument("dir", nargs="?", help="where to create it (default: current directory)")
    s.add_argument("--name", help="board name shown in the web UI (default: folder name)")
    s.set_defaults(func=cmd_init)

    # tasks
    task = sub.add_parser("task", help="manage tasks").add_subparsers(dest="task_cmd", required=True, parser_class=Parser)

    s = task.add_parser("new", help="create a task")
    s.add_argument("title")
    s.add_argument("--body", help="description text (Markdown)")
    s.add_argument("--body-file", help="read description from file ('-' for stdin)")
    s.add_argument("--status", default="backlog", help="backlog (default) | in-progress | done | cancelled")
    s.add_argument("--sprint", help="attach to sprint, e.g. S-001")
    s.add_argument("--priority", default="medium", choices=PRIORITIES)
    s.add_argument("--assignee")
    s.add_argument("--label", action="append", help="repeatable")
    s.add_argument("--by", default=DEFAULT_AUTHOR, help="author for the worklog entry")
    s.set_defaults(func=cmd_task_new)

    s = task.add_parser("list", help="list tasks (metadata only; use `show` for the brief)")
    s.add_argument("--status")
    s.add_argument("--sprint")
    s.add_argument("--assignee")
    s.set_defaults(func=cmd_task_list)

    s = task.add_parser("show", help="show brief and worklog")
    s.add_argument("id")
    s.set_defaults(func=cmd_task_show)

    s = task.add_parser("change-status", aliases=["status", "move"], help="change status (moves the folder)")
    s.add_argument("id")
    s.add_argument("status", help="backlog | in-progress | done | cancelled")
    s.add_argument("--note", help="extra text for the worklog entry")
    s.add_argument("--by", default=DEFAULT_AUTHOR)
    s.set_defaults(func=cmd_task_change_status)

    s = task.add_parser("assign", help="set the assignee (records it in the worklog)")
    s.add_argument("id")
    s.add_argument("assignee", nargs="?", default="", help="omit to unassign")
    s.add_argument("--by", default=DEFAULT_AUTHOR)
    s.set_defaults(func=cmd_task_assign)

    s = task.add_parser("log", help="append a worklog entry")
    s.add_argument("id")
    s.add_argument("message", help="Markdown text ('-' for stdin)")
    s.add_argument("--by", default=DEFAULT_AUTHOR)
    s.set_defaults(func=cmd_task_log)

    s = task.add_parser("edit", help="change metadata fields")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--priority", choices=PRIORITIES)
    s.add_argument("--assignee", help="use '' to clear")
    s.add_argument("--sprint", help="use '' to detach")
    s.add_argument("--label", action="append", help="repeatable; replaces all labels")
    s.set_defaults(func=cmd_task_edit)

    # sprints
    sprint = sub.add_parser("sprint", help="manage sprints").add_subparsers(dest="sprint_cmd", required=True, parser_class=Parser)

    s = sprint.add_parser("new", help="create a sprint")
    s.add_argument("title")
    s.add_argument("--goal")
    s.add_argument("--body", help="description text (Markdown)")
    s.add_argument("--body-file")
    s.add_argument("--status", default="backlog")
    s.add_argument("--start", help="YYYY-MM-DD")
    s.add_argument("--end", help="YYYY-MM-DD")
    s.set_defaults(func=cmd_sprint_new)

    s = sprint.add_parser("list", help="list sprints with progress")
    s.add_argument("--status")
    s.set_defaults(func=cmd_sprint_list)

    s = sprint.add_parser("show", help="sprint status and its tasks")
    s.add_argument("id")
    s.set_defaults(func=cmd_sprint_show)

    s = sprint.add_parser("change-status", aliases=["status", "move"], help="change sprint status (moves the folder)")
    s.add_argument("id")
    s.add_argument("status")
    s.set_defaults(func=cmd_sprint_change_status)

    s = sprint.add_parser("refresh", help="re-render the task checklist in sprint.md after hand edits")
    s.add_argument("id")
    s.set_defaults(func=cmd_sprint_refresh)

    s = sprint.add_parser("add", help="attach tasks to a sprint")
    s.add_argument("id")
    s.add_argument("tasks", nargs="+")
    s.set_defaults(func=cmd_sprint_add)

    s = sprint.add_parser("remove", help="detach tasks from a sprint")
    s.add_argument("id")
    s.add_argument("tasks", nargs="+")
    s.set_defaults(func=cmd_sprint_remove)

    # board-wide
    s = sub.add_parser("board", help="print a kanban view")
    s.add_argument("--sprint", help="only tasks of this sprint")
    s.set_defaults(func=cmd_board)

    s = sub.add_parser("check", help="validate consistency between tasks and sprints")
    s.add_argument("--fix", action="store_true", help="repair what can be repaired (stale sprint task lists)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("serve", help="run the web board")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8484)
    s.add_argument("--no-open", action="store_true", help="do not open a browser")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (BoardError, ValueError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
