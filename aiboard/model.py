"""Data model and Markdown front-matter handling for aiboard.

Everything on disk is plain Markdown with a small YAML-like front matter
block. Only a subset of YAML is supported on purpose: scalars, inline lists
(``[a, b]``) and block lists (``- a``). This keeps the files hand-editable
and the tool dependency-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATUSES: List[str] = ["backlog", "in-progress", "done", "cancelled"]
PRIORITIES: List[str] = ["low", "medium", "high"]
TASK_PREFIX = "T"
SPRINT_PREFIX = "S"
BRIEF_FILE = "brief.md"
WORKLOG_FILE = "worklog.md"
SPRINT_FILE = "sprint.md"

STATUS_ALIASES = {
    "todo": "backlog",
    "open": "backlog",
    "in_progress": "in-progress",
    "inprogress": "in-progress",
    "in progress": "in-progress",
    "wip": "in-progress",
    "doing": "in-progress",
    "started": "in-progress",
    "closed": "done",
    "complete": "done",
    "completed": "done",
    "canceled": "cancelled",
    "cancel": "cancelled",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_status(value: str) -> str:
    key = value.strip().lower()
    key = STATUS_ALIASES.get(key, key)
    if key not in STATUSES:
        raise ValueError(f"unknown status {value!r}; expected one of {', '.join(STATUSES)}")
    return key


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def format_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number:03d}"


def normalize_id(prefix: str, ref: str) -> Optional[str]:
    """Turn ``T-1``, ``t-001``, ``1`` or ``T-001-some-slug`` into ``T-001``."""
    ref = ref.strip()
    m = re.match(rf"^(?:{prefix}-?)?(\d+)(?:-.*)?$", ref, re.IGNORECASE)
    if not m:
        return None
    return format_id(prefix, int(m.group(1)))


# --------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------

def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "" or raw in ("null", "~", "None"):
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a Markdown document into (metadata, body)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key is not None:
            if not isinstance(meta.get(current_list_key), list):
                meta[current_list_key] = []
            meta[current_list_key].append(_parse_scalar(stripped[2:]))
            continue
        if stripped == "-" and current_list_key is not None:
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if value.strip() == "":
            # Either a null scalar or the start of a block list; decided by the next line.
            meta[key] = None
            current_list_key = key
        else:
            meta[key] = _parse_scalar(value)
            current_list_key = None
    body = "\n".join(lines[end + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def _dump_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if s == "" or s != s.strip() or any(c in s for c in ":#[]{}") or s.lower() in ("true", "false", "null"):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def dump_front_matter(meta: Dict[str, Any], body: str) -> str:
    out = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            if not value:
                out.append(f"{key}: []")
            else:
                out.append(f"{key}:")
                out.extend(f"  - {_dump_scalar(v)}" for v in value)
        else:
            out.append(f"{key}: {_dump_scalar(value)}")
    out.append("---")
    out.append("")
    out.append(body.rstrip("\n"))
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------

@dataclass
class WorklogEntry:
    timestamp: str
    author: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"timestamp": self.timestamp, "author": self.author, "text": self.text}


WORKLOG_HEADING = re.compile(r"^##\s+(\S+)\s+[—-]+\s+(.+?)\s*$")


def parse_worklog(text: str) -> List[WorklogEntry]:
    entries: List[WorklogEntry] = []
    current: Optional[WorklogEntry] = None
    buf: List[str] = []
    for line in text.splitlines():
        m = WORKLOG_HEADING.match(line)
        if m:
            if current is not None:
                current.text = "\n".join(buf).strip()
                entries.append(current)
            current = WorklogEntry(timestamp=m.group(1), author=m.group(2), text="")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        current.text = "\n".join(buf).strip()
        entries.append(current)
    return entries


def format_worklog_entry(timestamp: str, author: str, text: str) -> str:
    return f"## {timestamp} — {author}\n\n{text.strip()}\n\n"


@dataclass
class Task:
    id: str
    title: str
    status: str
    path: Path
    meta: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    worklog: List[WorklogEntry] = field(default_factory=list)

    @property
    def sprint(self) -> Optional[str]:
        return self.meta.get("sprint") or None

    @property
    def priority(self) -> str:
        return self.meta.get("priority") or "medium"

    @property
    def assignee(self) -> Optional[str]:
        return self.meta.get("assignee") or None

    @property
    def folder(self) -> str:
        return self.path.name

    def to_dict(self, include_body: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "sprint": self.sprint,
            "priority": self.priority,
            "assignee": self.assignee,
            "labels": list(self.meta.get("labels") or []),
            "created": self.meta.get("created"),
            "updated": self.meta.get("updated"),
            "folder": self.folder,
            "path": str(self.path),
        }
        if include_body:
            d["brief"] = self.body
            d["worklog"] = [e.to_dict() for e in self.worklog]
        return d


@dataclass
class Sprint:
    id: str
    title: str
    status: str
    path: Path
    meta: Dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def tasks(self) -> List[str]:
        return list(self.meta.get("tasks") or [])

    @property
    def folder(self) -> str:
        return self.path.name

    def to_dict(self, include_body: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "goal": self.meta.get("goal"),
            "start": self.meta.get("start"),
            "end": self.meta.get("end"),
            "tasks": self.tasks,
            "created": self.meta.get("created"),
            "updated": self.meta.get("updated"),
            "folder": self.folder,
            "path": str(self.path),
        }
        if include_body:
            d["description"] = self.body
        return d
