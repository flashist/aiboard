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
    and ``sprints/``.
    """
    here = Path(start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_FILE).is_file():
            return candidate
        if (candidate / "tasks").is_dir() and (candidate / "sprints").is_dir():
            return candidate
    return None


class Board:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lock_depth = 0
        self._lock_fd: Optional[int] = None
        self._config: Optional[Dict[str, Any]] = None

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
        if self._config is None:
            f = self.root / CONFIG_FILE
            try:
                self._config = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
            except ValueError as e:
                raise BoardError(f"{f} is not valid JSON: {e}")
        return self._config

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
            self._config = None
        return created

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
        title = meta.get("title") or _title_from_body(body) or path.name
        return Task(id=tid, title=str(title), status=status, path=path, meta=meta, body=body, worklog=worklog)

    def list_tasks(self, status: Optional[str] = None, sprint: Optional[str] = None) -> List[Task]:
        self._require()
        status = normalize_status(status) if status else None
        sprint_id = normalize_id(SPRINT_PREFIX, sprint) if sprint else None
        out = []
        for tid, st, path in self._scan(self.tasks_dir, TASK_PREFIX):
            if status and st != status:
                continue
            task = self._load_task(tid, st, path)
            if sprint_id and task.sprint != sprint_id:
                continue
            out.append(task)
        out.sort(key=lambda t: t.id)
        return out

    def get_task(self, ref: str) -> Task:
        self._require()
        tid, status, path = self._locate(self.tasks_dir, TASK_PREFIX, ref)
        return self._load_task(tid, status, path)

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
    ) -> Task:
        with self.write_lock():
            self._require()
            status = normalize_status(status)
            if priority not in PRIORITIES:
                raise BoardError(f"priority must be one of {', '.join(PRIORITIES)}")
            sprint_obj = self.get_sprint(sprint) if sprint else None
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
            for k, v in fields.items():
                if v is not None or k in meta:
                    meta[k] = v
            meta["updated"] = now_iso()
            body = task.body
            if "title" in fields and fields["title"]:
                body = re.sub(r"^# .*$", f"# {fields['title']}", body, count=1, flags=re.M)
            (task.path / BRIEF_FILE).write_text(dump_front_matter(meta, body), encoding="utf-8")
            return self.get_task(task.id)

    def move_task(self, ref: str, status: str, author: str = "aiboard", note: Optional[str] = None) -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            status = normalize_status(status)
            if status == task.status:
                return task
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

    def assign(self, ref: str, assignee: Optional[str], author: str = "aiboard") -> Task:
        with self.write_lock():
            task = self.get_task(ref)
            if task.assignee == assignee:
                return task
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
            if t.sprint:
                if t.sprint not in sprints:
                    problems.append(f"{t.id} references missing sprint {t.sprint}")
                elif t.id not in sprints[t.sprint].tasks:
                    problems.append(f"{t.id} says it is in {t.sprint}, but {t.sprint} does not list it")
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


def _title_from_body(body: str) -> Optional[str]:
    m = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    return m.group(1) if m else None
