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

from .store import Board, NotFound

WEB_DIR = Path(__file__).parent / "web"


def make_handler(board: Board):
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

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/board":
                    self._json(board.snapshot())
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


def serve(board: Board, host: str = "127.0.0.1", port: int = 8484, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(board))
    url = f"http://{host}:{port}/"
    print(f"aiboard web board: {url}  (root: {board.root})  Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
