"""Tiny read-only web board on top of the filesystem store.

No state of its own: every request re-reads the folders, so agents editing
files and humans looking at the board can never disagree.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .store import Board, BoardError, NotFound

WEB_DIR = Path(__file__).parent / "web"


def make_handler(board: Board, read_only: bool = False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter default log
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise BoardError("request body is not valid JSON")
            if not isinstance(data, dict):
                raise BoardError("request body must be a JSON object")
            return data

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if read_only:
                    raise BoardError("this board is served read-only")
                body = self._body()
                author = str(body.get("author") or "web").strip() or "web"
                parts = [p for p in path.split("/") if p]
                if parts == ["api", "tasks"]:
                    t = board.create_task(
                        title=str(body.get("title") or "").strip() or _fail("title is required"),
                        body=str(body.get("body") or ""), status=body.get("status") or "backlog",
                        sprint=body.get("sprint") or None, priority=body.get("priority") or "medium",
                        assignee=body.get("assignee") or None, labels=_labels(body.get("labels")), author=author,
                    )
                    self._json(t.to_dict(), HTTPStatus.CREATED)
                elif len(parts) == 4 and parts[:2] == ["api", "tasks"]:
                    tid, action = parts[2], parts[3]
                    if action == "status":
                        t = board.move_task(tid, str(body.get("status") or ""), author=author, note=body.get("note") or None,
                                            force=bool(body.get("force")))
                    elif action == "assign":
                        t = board.assign(tid, (body.get("assignee") or "").strip() or None, author=author, force=bool(body.get("force")))
                    elif action == "start":
                        t = board.start(tid, (body.get("assignee") or author).strip() or author, author=author, force=bool(body.get("force")))
                    elif action == "log":
                        t = board.log(tid, str(body.get("message") or "").strip() or _fail("message is required"), author=author)
                    elif action == "edit":
                        fields = {k: body[k] for k in ("title", "priority", "sprint", "labels", "blocked_by") if k in body}
                        if "labels" in fields:
                            fields["labels"] = _labels(fields["labels"])
                        if "blocked_by" in fields:
                            fields["blocked_by"] = _labels(fields["blocked_by"])
                        if "sprint" in fields and not fields["sprint"]:
                            fields["sprint"] = None
                        t = board.update_task(tid, **fields)
                    else:
                        raise NotFound(f"unknown action {action}")
                    self._json(t.to_dict(include_body=True))
                elif parts == ["api", "sprints"]:
                    sp = board.create_sprint(
                        title=str(body.get("title") or "").strip() or _fail("title is required"),
                        goal=str(body.get("goal") or ""), body=str(body.get("body") or ""),
                        status=body.get("status") or "backlog", start=body.get("start") or None, end=body.get("end") or None,
                    )
                    self._json(sp.to_dict(), HTTPStatus.CREATED)
                elif len(parts) == 4 and parts[:2] == ["api", "sprints"] and parts[3] == "status":
                    self._json(board.move_sprint(parts[2], str(body.get("status") or "")).to_dict())
                elif parts == ["api", "check", "fix"]:
                    problems = board.check(fix=True)
                    self._json({"ok": not [p for p in problems if not p.endswith("(fixed)")], "problems": problems})
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except NotFound as e:
                self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except (BoardError, ValueError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except Exception as e:  # pragma: no cover
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/board":
                    snap = board.snapshot()
                    snap["read_only"] = read_only
                    self._json(snap)
                elif path.startswith("/api/tasks/"):
                    task = board.get_task(path.rsplit("/", 1)[1])
                    self._json(task.to_dict(include_body=True))
                elif path.startswith("/api/sprints/"):
                    sprint = board.get_sprint(path.rsplit("/", 1)[1])
                    d = sprint.to_dict(include_body=True)
                    p = board.sprint_progress(sprint)
                    d["progress"] = {k: v for k, v in p.items() if k != "tasks"}
                    d["tasks_detail"] = [t.to_dict() for t in p["tasks"]]
                    self._json(d)
                elif path == "/api/check":
                    problems = board.check()
                    self._json({"ok": not problems, "problems": problems})
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except NotFound as e:
                self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except Exception as e:  # pragma: no cover - surfaced to the UI
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def _fail(message: str):
    raise BoardError(message)


def _labels(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(x).strip() for x in value if str(x).strip()]


def serve(board: Board, host: str = "127.0.0.1", port: int = 8484, open_browser: bool = True, read_only: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(board, read_only=read_only))
    url = f"http://{host}:{port}/"
    mode = "read-only" if read_only else "read-write"
    print(f"aiboard web board: {url}  (root: {board.root}, {mode})  Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
