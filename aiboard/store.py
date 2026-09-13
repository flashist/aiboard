"""Filesystem-backed board store.

Layout under the board root::

    tasks/<status>/<T-001-slug>/brief.md
    tasks/<status>/<T-001-slug>/worklog.md
    sprints/<status>/<S-001-slug>/sprint.md

The folder a task or sprint sits in *is* its status. Nothing else stores it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import (
    BRIEF_FILE,
    COMMENTS_FILE,
    DEFAULT_STALE_HOURS,
    PRIORITIES,
    SPRINT_FILE,
    SPRINT_PREFIX,
    STATUSES,
    TASK_PREFIX,
    WORKLOG_FILE,
    Sprint,
    Task,
    dump_front_matter,
    format_id,
    format_worklog_entry,
    normalize_id,
    normalize_status,
    now_iso,
    parse_front_matter,
    parse_iso,
    parse_worklog,
    slugify,
)


class BoardError(Exception):
    pass


class NotFound(BoardError):
    pass


ID_DIR = re.compile(r"^([A-Z])-(\d+)(?:-(.*))?$")
LOCK_FILE = ".aiboard.lock"
CONFIG_FILE = "aiboard.json"

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


def find_board_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (default: cwd) to find a board root, like git does.

    A directory is a board root when it holds ``aiboard.json`` or both ``tasks/``
    and ``sprints/``. An ``aiboard.json`` with a ``"board"`` key is a pointer to
    the board directory (relative to the file), which lets a project keep its
    board in a subfolder such as ``board/`` and still be found from anywhere.
    """
    here = Path(start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        cfg = candidate / CONFIG_FILE
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
            target = data.get("board") if isinstance(data, dict) else None
            if target:
                pointed = (candidate / str(target)).resolve()
                if pointed.is_dir():
                    return pointed
            return candidate
        if (candidate / "tasks").is_dir() and (candidate / "sprints").is_dir():
            return candidate
    return None


class Board:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lock_depth = 0
        self._lock_fd: Optional[int] = None

    @classmethod
    def locate(cls, root: Optional[str] = None) -> "Board":
        """Resolve the board: explicit root, then $AIBOARD_ROOT, then walk up from cwd."""
        if root:
            return cls(Path(root))
        env = os.environ.get("AIBOARD_ROOT")
        if env:
            return cls(Path(env))
        found = find_board_root()
        return cls(found if found else Path.cwd())

    @property
    def config(self) -> Dict[str, Any]:
        """Contents of aiboard.json (re-read on every access; it is tiny and may change)."""
        f = self.root / CONFIG_FILE
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        except ValueError as e:
            raise BoardError(f"{f} is not valid JSON: {e}")

    @property
    def name(self) -> str:
        return str(self.config.get("name") or self.root.name)

    @contextmanager
    def write_lock(self):
        """Serialise writers across processes (advisory file lock; reentrant per Board).

        Every mutating method takes this, so concurrent agents cannot hand out the
        same id twice or interleave a folder move with a sprint-file rewrite.
        """
        if self._lock_depth == 0:
            self._require()
            self._lock_fd = os.open(str(self.root / LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
            if fcntl is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0 and self._lock_fd is not None:
                if fcntl is not None:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None

    # ------------------------------------------------------------------ paths
    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def sprints_dir(self) -> Path:
        return self.root / "sprints"

    def exists(self) -> bool:
        return self.tasks_dir.is_dir() and self.sprints_dir.is_dir()

    def init(self, name: Optional[str] = None) -> List[Path]:
        created = []
        self.root.mkdir(parents=True, exist_ok=True)
        for base in (self.tasks_dir, self.sprints_dir):
            for status in STATUSES:
                p = base / status
                if not p.exists():
                    p.mkdir(parents=True)
                    created.append(p)
                keep = p / ".gitkeep"
                if not keep.exists():
                    keep.touch()
        cfg = self.root / CONFIG_FILE
        if not cfg.exists():
            cfg.write_text(json.dumps({"name": name or self.root.name, "version": 1}, indent=2) + "\n", encoding="utf-8")
            created.append(cfg)
        return created

    def write_pointer(self, project_dir: Path) -> Optional[Path]:
        """Write ``<project_dir>/aiboard.json`` = {"board": "<relative path>"} so the
        board is discoverable from the whole project. No-op if a config exists there."""
        project_dir = Path(project_dir).resolve()
        if project_dir == self.root:
            return None
        pointer = project_dir / CONFIG_FILE
        if pointer.exists():
            return None
        try:
            rel = self.root.relative_to(project_dir)
        except ValueError:
            return None
        pointer.write_text(json.dumps({"board": rel.as_posix()}, indent=2) + "\n", encoding="utf-8")
        return pointer

    def _require(self) -> None:
        if not self.exists():
            raise BoardError(f"no board at {self.root} (run `aiboard init` first)")

    # -------------------------------------------------------------- scanning
    def _scan(self, base: Path, prefix: str) -> Iterable[Tuple[str, str, Path]]:
        """Yield (id, status, path) for every entity folder under base."""
        for status in STATUSES:
            status_dir = base / status
            if not status_dir.is_dir():
                continue
            for child in sorted(status_dir.iterdir()):
                if not child.is_dir():
                    continue
                m = ID_DIR.match(child.name)
                if not m or m.group(1) != prefix:
                    continue
                yield format_id(prefix, int(m.group(2))), status, child

    def _locate(self, base: Path, prefix: str, ref: str) -> Tuple[str, str, Path]:
        wanted = normalize_id(prefix, ref)
        if wanted is None:
            raise NotFound(f"{ref!r} is not a valid {prefix}-id")
        for eid, status, path in self._scan(base, prefix):
            if eid == wanted:
                return eid, status, path
        raise NotFound(f"{wanted} not found under {base}")

    def _next_number(self, base: Path, prefix: str) -> int:
        numbers = [int(eid.split("-")[1]) for eid, _, _ in self._scan(base, prefix)]
        return (max(numbers) + 1) if numbers else 1

    # ----------------------------------------------------------------- tasks
    def _load_task(self, tid: str, status: str, path: Path) -> Task:
        brief = path / BRIEF_FILE
        meta, body = parse_front_matter(brief.read_text(encoding="utf-8")) if brief.exists() else ({}, "")
        worklog_path = path / WORKLOG_FILE
        worklog = parse_worklog(worklog_path.read_text(encoding="utf-8")) if worklog_path.exists() else []
        comments_path = path / COMMENTS_FILE
        comments = parse_worklog(comments_path.read_text(encoding="utf-8")) if comments_path.exists() else []
        title = meta.get("title") or _title_from_body(body) or path.name
        task = Task(id=tid, title=str(title), status=status, path=path, meta=meta, body=body, worklog=worklog, comments=comments)
        task.stale = self._is_stale(task)
        return task

    @property
    def stale_after_hours(self) -> float:
        try:
            return float(self.config.get("stale_after_hours", DEFAULT_STALE_HOURS))
        except (TypeError, ValueError):
            return float(DEFAULT_STALE_HOURS)

    def _is_stale(self, task: Task) -> bool:
        """In progress with no worklog entry or comment for longer than stale_after_hours."""
        if task.status != "in-progress":
            return False
        last = parse_iso(task.last_activity)
        if last is None:
            return True
        from datetime import datetime, timedelta, timezone

        return datetime.now(timezone.utc) - last > timedelta(hours=self.stale_after_hours)

    def _status_index(self) -> Dict[str, Tuple[str, str]]:
        """id -> (status, title) for every task, without parsing worklogs."""
        index: Dict[str, Tuple[str, str]] = {}
        for tid, st, path in self._scan(self.tasks_dir, TASK_PREFIX):
            brief = path / BRIEF_FILE
            title = path.name
            if brief.exists():
                meta, body = parse_front_matter(brief.read_text(encoding="utf-8"))
                title = str(meta.get("title") or _title_from_body(body) or path.name)
            index[tid] = (st, title)
        return index

    def _resolve_blockers(self, task: Task, index: Optional[Dict[str, Tuple[str, str]]] = None) -> Task:
        ids = task.blocked_by
        if not ids:
            task.blockers = []
            return task
        index = index if index is not None else self._status_index()
        task.blockers = [
            {"id": b, "status": index[b][0], "title": index[b][1]} if b in index else {"id": b, "status": "missing", "title": None}
            for b in ids
        ]
        return task

    def list_tasks(self, status: Optional[str] = None, sprint: Optional[str] = None,
                   unblocked: bool = False, stale: bool = False) -> List[Task]:
        self._require()
        status = normalize_status(status) if status else None
        sprint_id = normalize_id(SPRINT_PREFIX, sprint) if sprint else None
        index = self._status_index()
        out = []
        for tid, st, path in self._scan(self.tasks_dir, TASK_PREFIX):
            if status and st != status:
                continue
            task = self._load_task(tid, st, path)
            if sprint_id and task.sprint != sprint_id:
                continue
            self._resolve_blockers(task, index)
            if unblocked and task.blocked:
                continue
            if stale and not task.stale:
                continue
            out.append(task)
        out.sort(key=lambda t: t.id)
        return out

    def get_task(self, ref: str) -> Task:
        self._require()
        tid, status, path = self._locate(self.tasks_dir, TASK_PREFIX, ref)
        return self._resolve_blockers(self._load_task(tid, status, path))

    def create_task(
        self,
        title: str,
        body: str = "",
        status: str = "backlog",
        sprint: Optional[str] = None,
        priority: str = "medium",
        assignee: Optional[str] = None,
        labels: Optional[List[str]] = None,
        author: str = "aiboard",
        blocked_by: Optional[List[str]] = None,
    ) -> Task:
        with self.write_lock():
            self._require()
            status = normalize_status(status)
            if priority not in PRIORITIES:
                raise BoardError(f"priority must be one of {', '.join(PRIORITIES)}")
            sprint_obj = self.get_sprint(sprint) if sprint else None
            blockers = self._normalize_blockers(blocked_by or [], None)
            tid = format_id(TASK_PREFIX, self._next_number(self.tasks_dir, TASK_PREFIX))
            folder = self.tasks_dir / status / f"{tid}-{slugify(title)}"
            folder.mkdir(parents=True)
            ts = now_iso()
            meta: Dict[str, Any] = {
                "id": tid,
                "title": title,
                "sprint": sprint_obj.id if sprint_obj else None,
                "priority": priority,
                "assignee": assignee,
                "labels": labels or [],
                "blocked_by": blockers,
                "created": ts,
                "updated": ts,
            }
            body = body.strip() or "_No description yet._"
            (folder / BRIEF_FILE).write_text(dump_front_matter(meta, f"# {title}\n\n{body}\n"), encoding="utf-8")
            (folder / WORKLOG_FILE).write_text(
                "# Worklog\n\n" + format_worklog_entry(ts, author, f"Task created in `{status}`."), encoding="utf-8"
            )
            if sprint_obj:
                self._sprint_set_tasks(sprint_obj, sprint_obj.tasks + [tid])
            return self.get_task(tid)

    def update_task(self, ref: str, **fields: Any) -> Task:
        """Update front-matter fields. Passing ``sprint`` keeps sprint files in sync."""
        with self.write_lock():
            task = self.get_task(ref)
            meta = dict(task.meta)
            if "sprint" in fields:
                new_sprint = fields.pop("sprint")
                new_id = self.get_sprint(new_sprint).id if new_sprint else None
                if task.sprint and task.sprint != new_id:
                    try:
                        old = self.get_sprint(task.sprint)
                        self._sprint_set_tasks(old, [t for t in old.tasks if t != task.id])
                    except NotFound:
                        pass
                if new_id:
                    sp = self.get_sprint(new_id)
                    if task.id not in sp.tasks:
                        self._sprint_set_tasks(sp, sp.tasks + [task.id])
                meta["sprint"] = new_id
            if "priority" in fields and fields["priority"] not in PRIORITIES:
                raise BoardError(f"priority must be one of {', '.join(PRIORITIES)}")
            if "blocked_by" in fields:
                fields["blocked_by"] = self._normalize_blockers(fields["blocked_by"] or [], task.id)
            for k, v in fields.items():
                if v is not None or k in meta:
                    meta[k] = v
            meta["updated"] = now_iso()
            body = task.body
            if "title" in fields and fields["title"]:
                body = re.sub(r"^# .*$", f"# {fields['title']}", body, count=1, flags=re.M)
            (task.path / BRIEF_FILE).write_text(dump_front_matter(meta, body), encoding="utf-8")
            return self.get_task(task.id)

    def move_task(self, ref: str, status: str, author: str = "aiboard", note: Optional[str] = None,
                  force: bool = False) -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            status = normalize_status(status)
            if status == task.status:
                return task
            if status == "in-progress" and task.blocked and not force:
                open_ids = [b["id"] for b in task.blockers if b["status"] not in ("done", "cancelled")]
                raise BoardError(f"{task.id} is blocked by {', '.join(open_ids)}; finish those first or pass --force")
            dest = self.tasks_dir / status / task.path.name
            if dest.exists():
                raise BoardError(f"destination already exists: {dest}")
            shutil.move(str(task.path), str(dest))
            moved = self._load_task(task.id, status, dest)
            message = f"Status changed `{task.status}` → `{status}`."
            if note:
                message += f" {note}"
            self._append_worklog(moved, author, message)
            self._touch(moved)
            if moved.sprint:
                try:
                    self.refresh_sprint(moved.sprint)
                except NotFound:
                    pass
            return self.get_task(task.id)

    def _normalize_blockers(self, refs: List[str], self_id: Optional[str]) -> List[str]:
        out: List[str] = []
        index = self._status_index()
        for ref in refs:
            tid = normalize_id(TASK_PREFIX, str(ref))
            if tid is None:
                raise BoardError(f"{ref!r} is not a valid task id")
            if tid == self_id:
                raise BoardError(f"{tid} cannot block itself")
            if tid not in index:
                raise NotFound(f"{tid} not found under {self.tasks_dir}")
            if tid not in out:
                out.append(tid)
        return out

    def block(self, ref: str, blocker_refs: List[str], author: str = "aiboard") -> Task:
        """Add blockers (Jira: 'is blocked by')."""
        with self.write_lock():
            task = self.get_task(ref)
            new = self._normalize_blockers(blocker_refs, task.id)
            added = [b for b in new if b not in task.blocked_by]
            if not added:
                return task
            self.update_task(task.id, blocked_by=task.blocked_by + added)
            self._append_worklog(self.get_task(task.id), author, f"Blocked by {', '.join(added)}.")
            return self.get_task(task.id)

    def unblock(self, ref: str, blocker_refs: List[str], author: str = "aiboard") -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            drop = [normalize_id(TASK_PREFIX, r) for r in blocker_refs]
            removed = [b for b in task.blocked_by if b in drop]
            if not removed:
                return task
            self.update_task(task.id, blocked_by=[b for b in task.blocked_by if b not in drop])
            self._append_worklog(self.get_task(task.id), author, f"No longer blocked by {', '.join(removed)}.")
            return self.get_task(task.id)

    def start(self, ref: str, assignee: str, author: str = "aiboard", force: bool = False) -> Task:
        """Jira's "Start progress", done atomically: claim the task and move it to in-progress.

        Fails unless the task is in backlog and unassigned (or already assigned
        to ``assignee``). This is the safe way for concurrent agents to pick work.
        """
        with self.write_lock():
            task = self.get_task(ref)
            if task.status != "backlog" and not force:
                who = f" by {task.assignee}" if task.assignee else ""
                raise BoardError(f"{task.id} is {task.status}{who}, not in backlog; pick another task or pass --force")
            if task.assignee and task.assignee != assignee and not force:
                raise BoardError(f"{task.id} is already assigned to {task.assignee}; pick another task or pass --force")
            if task.blocked and not force:
                open_ids = [b["id"] for b in task.blockers if b["status"] not in ("done", "cancelled")]
                raise BoardError(f"{task.id} is blocked by {', '.join(open_ids)}; finish those first or pass --force")
            if task.assignee != assignee:
                self.update_task(task.id, assignee=assignee)
                self._append_worklog(self.get_task(task.id), author, f"Assigned to `{assignee}`.")
            return self.move_task(task.id, "in-progress", author=author, force=True)

    def assign(self, ref: str, assignee: Optional[str], author: str = "aiboard", force: bool = False) -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            if task.assignee == assignee:
                return task
            if task.assignee and assignee and task.assignee != author and not force:
                raise BoardError(f"{task.id} is already assigned to {task.assignee}; pass --force to reassign")
            self.update_task(task.id, assignee=assignee)
            who = f"`{assignee}`" if assignee else "nobody"
            self._append_worklog(self.get_task(task.id), author, f"Assigned to {who}.")
            return self.get_task(task.id)

    def log(self, ref: str, message: str, author: str = "aiboard") -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            self._append_worklog(task, author, message)
            self._touch(task)
            return self.get_task(task.id)

    def comment(self, ref: str, message: str, author: str = "aiboard") -> Task:
        """Append to the task's discussion (comments.md), separate from the worker's worklog."""
        with self.write_lock():
            task = self.get_task(ref)
            path = task.path / COMMENTS_FILE
            existing = path.read_text(encoding="utf-8") if path.exists() else "# Comments\n\n"
            if not existing.endswith("\n\n"):
                existing = existing.rstrip("\n") + "\n\n"
            path.write_text(existing + format_worklog_entry(now_iso(), author, message), encoding="utf-8")
            self._touch(task)
            return self.get_task(task.id)

    def _append_worklog(self, task: Task, author: str, message: str) -> None:
        path = task.path / WORKLOG_FILE
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Worklog\n\n"
        if not existing.endswith("\n\n"):
            existing = existing.rstrip("\n") + "\n\n"
        path.write_text(existing + format_worklog_entry(now_iso(), author, message), encoding="utf-8")

    def _touch(self, task: Task) -> None:
        meta = dict(task.meta)
        meta["updated"] = now_iso()
        (task.path / BRIEF_FILE).write_text(dump_front_matter(meta, task.body), encoding="utf-8")

    # --------------------------------------------------------------- sprints
    def _load_sprint(self, sid: str, status: str, path: Path) -> Sprint:
        f = path / SPRINT_FILE
        meta, body = parse_front_matter(f.read_text(encoding="utf-8")) if f.exists() else ({}, "")
        title = meta.get("title") or _title_from_body(body) or path.name
        return Sprint(id=sid, title=str(title), status=status, path=path, meta=meta, body=body)

    def list_sprints(self, status: Optional[str] = None) -> List[Sprint]:
        self._require()
        status = normalize_status(status) if status else None
        out = [
            self._load_sprint(sid, st, path)
            for sid, st, path in self._scan(self.sprints_dir, SPRINT_PREFIX)
            if not status or st == status
        ]
        out.sort(key=lambda s: s.id)
        return out

    def get_sprint(self, ref: str) -> Sprint:
        self._require()
        sid, status, path = self._locate(self.sprints_dir, SPRINT_PREFIX, ref)
        return self._load_sprint(sid, status, path)

    def create_sprint(
        self,
        title: str,
        goal: str = "",
        body: str = "",
        status: str = "backlog",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Sprint:
        with self.write_lock():
            self._require()
            status = normalize_status(status)
            sid = format_id(SPRINT_PREFIX, self._next_number(self.sprints_dir, SPRINT_PREFIX))
            folder = self.sprints_dir / status / f"{sid}-{slugify(title)}"
            folder.mkdir(parents=True)
            ts = now_iso()
            meta: Dict[str, Any] = {
                "id": sid,
                "title": title,
                "goal": goal or None,
                "start": start,
                "end": end,
                "tasks": [],
                "created": ts,
                "updated": ts,
            }
            text = f"# {title}\n\n"
            if goal:
                text += f"**Goal:** {goal}\n\n"
            text += (body.strip() + "\n\n") if body.strip() else ""
            text += "## Tasks\n\n_No tasks attached yet._\n"
            (folder / SPRINT_FILE).write_text(dump_front_matter(meta, text), encoding="utf-8")
            return self.get_sprint(sid)

    def move_sprint(self, ref: str, status: str) -> Sprint:
        with self.write_lock():
            sprint = self.get_sprint(ref)
            status = normalize_status(status)
            if status == sprint.status:
                return sprint
            dest = self.sprints_dir / status / sprint.path.name
            if dest.exists():
                raise BoardError(f"destination already exists: {dest}")
            shutil.move(str(sprint.path), str(dest))
            moved = self._load_sprint(sprint.id, status, dest)
            self._write_sprint(moved, moved.meta, moved.body)
            return self.get_sprint(sprint.id)

    def sprint_add(self, sprint_ref: str, task_refs: List[str]) -> Sprint:
        with self.write_lock():
            sprint = self.get_sprint(sprint_ref)
            for ref in task_refs:
                self.update_task(ref, sprint=sprint.id)
            return self.get_sprint(sprint.id)

    def sprint_remove(self, sprint_ref: str, task_refs: List[str]) -> Sprint:
        with self.write_lock():
            sprint = self.get_sprint(sprint_ref)
            for ref in task_refs:
                task = self.get_task(ref)
                if task.sprint == sprint.id:
                    self.update_task(task.id, sprint=None)
                else:
                    sprint = self.get_sprint(sprint.id)
                    self._sprint_set_tasks(sprint, [t for t in sprint.tasks if t != task.id])
            return self.get_sprint(sprint.id)

    def _sprint_set_tasks(self, sprint: Sprint, task_ids: List[str]) -> None:
        seen: List[str] = []
        for t in task_ids:
            if t not in seen:
                seen.append(t)
        meta = dict(sprint.meta)
        meta["tasks"] = seen
        self._write_sprint(sprint, meta, sprint.body)

    def _write_sprint(self, sprint: Sprint, meta: Dict[str, Any], body: str) -> None:
        meta = dict(meta)
        meta["updated"] = now_iso()
        body = self._render_sprint_task_list(meta.get("tasks") or [], body)
        (sprint.path / SPRINT_FILE).write_text(dump_front_matter(meta, body), encoding="utf-8")

    def _render_sprint_task_list(self, task_ids: List[str], body: str) -> str:
        """Regenerate the human-readable '## Tasks' section of sprint.md."""
        lines = []
        for tid in task_ids:
            try:
                t = self.get_task(tid)
                box = "x" if t.status == "done" else ("-" if t.status == "cancelled" else " ")
                lines.append(f"- [{box}] {t.id} — {t.title} ({t.status})")
            except NotFound:
                lines.append(f"- [ ] {tid} — _missing_")
        section = "## Tasks\n\n" + ("\n".join(lines) if lines else "_No tasks attached yet._") + "\n"
        pattern = re.compile(r"^## Tasks\s*\n.*?(?=^## |\Z)", re.M | re.S)
        if pattern.search(body):
            return pattern.sub(lambda _m: section, body, count=1)
        return body.rstrip("\n") + "\n\n" + section

    def refresh_sprint(self, ref: str) -> Sprint:
        """Re-render the task list in sprint.md (e.g. after tasks changed status)."""
        with self.write_lock():
            sprint = self.get_sprint(ref)
            self._write_sprint(sprint, sprint.meta, sprint.body)
            return self.get_sprint(sprint.id)

    def sprint_progress(self, sprint: Sprint) -> Dict[str, Any]:
        counts = {s: 0 for s in STATUSES}
        tasks = []
        for tid in sprint.tasks:
            try:
                t = self.get_task(tid)
                counts[t.status] += 1
                tasks.append(t)
            except NotFound:
                continue
        total = len(tasks)
        finished = counts["done"] + counts["cancelled"]
        return {
            "total": total,
            "counts": counts,
            "percent": int(round(100 * counts["done"] / total)) if total else 0,
            "remaining": total - finished,
            "tasks": tasks,
        }

    # ----------------------------------------------------------------- check
    def check(self, fix: bool = False) -> List[str]:
        """Return a list of human-readable consistency problems.

        With ``fix=True`` the repairable ones (stale sprint task lists) are
        repaired and reported as fixed.
        """
        self._require()
        problems: List[str] = []
        tasks = {t.id: t for t in self.list_tasks()}
        sprints = {s.id: s for s in self.list_sprints()}
        seen_dirs: Dict[str, Path] = {}
        for tid, _, path in self._scan(self.tasks_dir, TASK_PREFIX):
            if tid in seen_dirs:
                problems.append(f"{tid} exists twice: {seen_dirs[tid]} and {path}")
            seen_dirs[tid] = path
            if not (path / BRIEF_FILE).exists():
                problems.append(f"{tid} has no {BRIEF_FILE}")
            if not (path / WORKLOG_FILE).exists():
                problems.append(f"{tid} has no {WORKLOG_FILE}")
        for t in tasks.values():
            if t.meta.get("id") and t.meta["id"] != t.id:
                problems.append(f"{t.id}: front matter id is {t.meta['id']!r}")
            for b in t.blocked_by:
                if b == t.id:
                    problems.append(f"{t.id} is blocked by itself")
                elif b not in tasks:
                    problems.append(f"{t.id} is blocked by missing task {b}")
            if t.stale:
                problems.append(
                    f"{t.id} is in progress but has had no worklog entry or comment since {t.last_activity or 'unknown'}"
                    f" (stale after {self.stale_after_hours:g}h; assignee {t.assignee or 'nobody'})"
                )
            if t.status == "in-progress" and t.blocked:
                open_ids = [b["id"] for b in t.blockers if b["status"] not in ("done", "cancelled")]
                problems.append(f"{t.id} is in progress but blocked by {', '.join(open_ids)}")
            if t.sprint:
                if t.sprint not in sprints:
                    problems.append(f"{t.id} references missing sprint {t.sprint}")
                elif t.id not in sprints[t.sprint].tasks:
                    problems.append(f"{t.id} says it is in {t.sprint}, but {t.sprint} does not list it")
        for cycle in _find_cycles({t.id: [b for b in t.blocked_by if b in tasks] for t in tasks.values()}):
            problems.append("dependency cycle: " + " -> ".join(cycle))
        for s in sprints.values():
            if not (s.path / SPRINT_FILE).exists():
                problems.append(f"{s.id} has no {SPRINT_FILE}")
            for tid in s.tasks:
                if tid not in tasks:
                    problems.append(f"{s.id} lists missing task {tid}")
                elif tasks[tid].sprint != s.id:
                    problems.append(f"{s.id} lists {tid}, but {tid} says sprint={tasks[tid].sprint}")
            if s.status == "done":
                open_tasks = [tid for tid in s.tasks if tid in tasks and tasks[tid].status in ("backlog", "in-progress")]
                if open_tasks:
                    problems.append(f"{s.id} is done but still has open tasks: {', '.join(open_tasks)}")
            fresh = self._render_sprint_task_list(s.tasks, s.body)
            if fresh.strip() != s.body.strip():
                if fix:
                    self.refresh_sprint(s.id)
                    problems.append(f"{s.id}: task list in {SPRINT_FILE} was stale (fixed)")
                else:
                    problems.append(
                        f"{s.id}: task list in {SPRINT_FILE} is stale; run `aiboard sprint refresh {s.id}` or `aiboard check --fix`"
                    )
        return problems

    # --------------------------------------------------------------- summary
    def snapshot(self) -> Dict[str, Any]:
        """Everything the web board needs, in one JSON-able dict."""
        tasks = self.list_tasks()
        sprints = []
        for s in self.list_sprints():
            p = self.sprint_progress(s)
            d = s.to_dict()
            d["progress"] = {k: v for k, v in p.items() if k != "tasks"}
            sprints.append(d)
        return {
            "root": str(self.root),
            "name": self.name,
            "statuses": STATUSES,
            "tasks": [t.to_dict() for t in tasks],
            "sprints": sprints,
            "generated": now_iso(),
        }


def _find_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    """Return each elementary cycle once, as a list of ids ending where it started."""
    cycles: List[List[str]] = []
    seen_keys = set()
    state: Dict[str, int] = {}
    stack: List[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt, 0) == 0:
                visit(nxt)
            elif state.get(nxt) == 1:
                cyc = stack[stack.index(nxt):] + [nxt]
                key = frozenset(cyc)
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(cyc)
        stack.pop()
        state[node] = 2

    for n in sorted(graph):
        if state.get(n, 0) == 0:
            visit(n)
    return cycles


def _title_from_body(body: str) -> Optional[str]:
    m = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    return m.group(1) if m else None
