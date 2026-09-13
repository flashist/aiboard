"""Model Context Protocol server over stdio, with no dependencies.

Any MCP-capable agent (Claude Code, Codex, Cursor, Gemini CLI, ...) can use
the board through tool calls instead of shelling out. Transport: one JSON-RPC
2.0 message per line on stdin/stdout, as the MCP stdio transport specifies.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .model import PRIORITIES, STATUSES
from .store import Board, BoardError, NotFound

PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = """aiboard is a file-based issue tracker: tasks and sprints are folders, the
parent folder is the status (backlog, in-progress, done, cancelled).
Typical loop: list_tasks(status="backlog") -> assign_task -> change_task_status(in-progress)
-> get_task (read the brief) -> log_work as you go -> change_task_status(done, note=...).
Worklogs are append-only; always log decisions and blockers so humans can follow.
Run check_board when you have edited files by hand."""


def _str(desc: str, **extra: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": "string", "description": desc}
    d.update(extra)
    return d


STATUS_PROP = _str("backlog | in-progress | done | cancelled (aliases like 'wip', 'todo', 'closed' accepted)")
ID_PROP = _str("task id such as T-007 (T-7 and 7 also work)")
SPRINT_ID_PROP = _str("sprint id such as S-001")


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


class Server:
    def __init__(self, board: Board, author: str):
        self.board = board
        self.author = author
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.handlers: Dict[str, Callable[..., Any]] = {}
        self._register_tools()

    # ------------------------------------------------------------ tools
    def tool(self, name: str, description: str, schema: Dict[str, Any]):
        def deco(fn: Callable[..., Any]):
            self.tools[name] = {"name": name, "description": description, "inputSchema": schema}
            self.handlers[name] = fn
            return fn
        return deco

    def _register_tools(self) -> None:
        b = self.board
        t = self.tool

        @t("board_summary", "Board name, task counts per status, and every sprint with its progress.", _schema({}))
        def board_summary():
            snap = b.snapshot()
            counts = {s: sum(1 for x in snap["tasks"] if x["status"] == s) for s in STATUSES}
            return {"name": snap["name"], "root": snap["root"], "task_counts": counts, "sprints": snap["sprints"]}

        @t("list_tasks", "List tasks (metadata only; use get_task for the brief and worklog).",
           _schema({"status": STATUS_PROP, "sprint": SPRINT_ID_PROP, "assignee": _str("filter by assignee")}))
        def list_tasks(status=None, sprint=None, assignee=None):
            tasks = b.list_tasks(status=status, sprint=sprint)
            if assignee:
                tasks = [x for x in tasks if x.assignee == assignee]
            return [x.to_dict() for x in tasks]

        @t("get_task", "Full task: metadata, brief (Markdown) and worklog entries.", _schema({"id": ID_PROP}, ["id"]))
        def get_task(id):
            return b.get_task(id).to_dict(include_body=True)

        @t("create_task", "Create a task. Returns the new task; its id is allocated automatically.",
           _schema({
               "title": _str("short imperative title"),
               "body": _str("Markdown description: context, acceptance criteria, links"),
               "sprint": SPRINT_ID_PROP,
               "priority": _str("low | medium | high", enum=PRIORITIES),
               "assignee": _str("who works on it"),
               "labels": {"type": "array", "items": {"type": "string"}, "description": "free-form labels"},
               "status": STATUS_PROP,
           }, ["title"]))
        def create_task(title, body="", sprint=None, priority="medium", assignee=None, labels=None, status="backlog"):
            return b.create_task(title, body=body, status=status, sprint=sprint, priority=priority,
                                 assignee=assignee, labels=labels or [], author=self.author).to_dict()

        @t("change_task_status", "Move a task to another status (moves its folder, appends a worklog entry).",
           _schema({"id": ID_PROP, "status": STATUS_PROP, "note": _str("optional text added to the worklog entry")}, ["id", "status"]))
        def change_task_status(id, status, note=None):
            return b.move_task(id, status, author=self.author, note=note).to_dict()

        @t("assign_task", "Set or clear the assignee of a task.",
           _schema({"id": ID_PROP, "assignee": _str("name; omit or empty to unassign")}, ["id"]))
        def assign_task(id, assignee=None):
            return b.assign(id, assignee or None, author=self.author).to_dict()

        @t("log_work", "Append an entry to the task's worklog (Markdown). Use it for progress, decisions, blockers.",
           _schema({"id": ID_PROP, "message": _str("Markdown text")}, ["id", "message"]))
        def log_work(id, message):
            task = b.log(id, message, author=self.author)
            return {"id": task.id, "entries": len(task.worklog), "last": task.worklog[-1].to_dict()}

        @t("edit_task", "Change task metadata. Only the fields given are changed; sprint='' detaches.",
           _schema({"id": ID_PROP, "title": _str("new title"), "priority": _str("low | medium | high", enum=PRIORITIES),
                    "sprint": SPRINT_ID_PROP, "labels": {"type": "array", "items": {"type": "string"}}}, ["id"]))
        def edit_task(id, **fields):
            if "sprint" in fields and not fields["sprint"]:
                fields["sprint"] = None
            return b.update_task(id, **fields).to_dict()

        @t("list_sprints", "List sprints with progress.", _schema({"status": STATUS_PROP}))
        def list_sprints(status=None):
            out = []
            for s in b.list_sprints(status=status):
                d = s.to_dict()
                d["progress"] = {k: v for k, v in b.sprint_progress(s).items() if k != "tasks"}
                out.append(d)
            return out

        @t("get_sprint", "Sprint details, progress, and its tasks.", _schema({"id": SPRINT_ID_PROP}, ["id"]))
        def get_sprint(id):
            s = b.get_sprint(id)
            p = b.sprint_progress(s)
            d = s.to_dict(include_body=True)
            d["progress"] = {k: v for k, v in p.items() if k != "tasks"}
            d["tasks_detail"] = [x.to_dict() for x in p["tasks"]]
            return d

        @t("create_sprint", "Create a sprint.",
           _schema({"title": _str("sprint name"), "goal": _str("one-line goal"), "body": _str("Markdown description"),
                    "start": _str("YYYY-MM-DD"), "end": _str("YYYY-MM-DD"), "status": STATUS_PROP}, ["title"]))
        def create_sprint(title, goal="", body="", start=None, end=None, status="backlog"):
            return b.create_sprint(title, goal=goal, body=body, status=status, start=start, end=end).to_dict()

        @t("change_sprint_status", "Move a sprint to another status.",
           _schema({"id": SPRINT_ID_PROP, "status": STATUS_PROP}, ["id", "status"]))
        def change_sprint_status(id, status):
            return b.move_sprint(id, status).to_dict()

        @t("sprint_add_tasks", "Attach tasks to a sprint.",
           _schema({"sprint": SPRINT_ID_PROP, "tasks": {"type": "array", "items": ID_PROP}}, ["sprint", "tasks"]))
        def sprint_add_tasks(sprint, tasks):
            return b.sprint_add(sprint, tasks).to_dict()

        @t("sprint_remove_tasks", "Detach tasks from a sprint.",
           _schema({"sprint": SPRINT_ID_PROP, "tasks": {"type": "array", "items": ID_PROP}}, ["sprint", "tasks"]))
        def sprint_remove_tasks(sprint, tasks):
            return b.sprint_remove(sprint, tasks).to_dict()

        @t("check_board", "Validate consistency between tasks and sprints; fix=true repairs stale sprint checklists.",
           _schema({"fix": {"type": "boolean", "description": "repair what can be repaired"}}))
        def check_board(fix=False):
            problems = b.check(fix=bool(fix))
            return {"ok": not [p for p in problems if not p.endswith("(fixed)")], "problems": problems}

    # ---------------------------------------------------------- protocol
    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return None  # a response to something we never sent
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "aiboard", "version": __version__},
                    "instructions": INSTRUCTIONS + f"\nBoard: {self.board.name} at {self.board.root}",
                }
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": list(self.tools.values())}
            elif method == "tools/call":
                result = self._call(params.get("name"), params.get("arguments") or {})
            else:
                return _error(msg_id, -32601, f"method not found: {method}")
        except _ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # noqa: BLE001 - surface as JSON-RPC error
            return _error(msg_id, -32603, f"{type(e).__name__}: {e}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _call(self, name: Optional[str], arguments: Dict[str, Any]) -> Dict[str, Any]:
        fn = self.handlers.get(name or "")
        if fn is None:
            raise _ToolError(f"unknown tool: {name}")
        try:
            data = fn(**arguments)
        except (BoardError, NotFound, ValueError, TypeError) as e:
            raise _ToolError(f"{type(e).__name__}: {e}")
        return {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
            "structuredContent": data if isinstance(data, dict) else {"result": data},
        }

    def serve_stdio(self, inp=None, out=None) -> None:
        inp = inp or sys.stdin
        out = out or sys.stdout
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                reply = _error(None, -32700, "parse error")
            else:
                reply = self.handle(msg)
            if reply is not None:
                out.write(json.dumps(reply, ensure_ascii=False) + "\n")
                out.flush()


class _ToolError(Exception):
    pass


def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main(board: Board, author: Optional[str] = None) -> None:
    author = author or os.environ.get("AIBOARD_AUTHOR") or "mcp-agent"
    Server(board, author).serve_stdio()
