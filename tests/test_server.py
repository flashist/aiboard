import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from aiboard.server import make_handler
from aiboard.store import Board


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.board = Board(self.tmp)
        self.board.init(name="Srv")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def req(self, path, data=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps({"author": "mark", **data}).encode() if data is not None else None
        r = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"} if body else {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_index_and_board(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as resp:
            self.assertIn(b"<title>aiboard</title>", resp.read())
        status, snap = self.req("/api/board")
        self.assertEqual((status, snap["name"], snap["read_only"]), (200, "Srv", False))

    def test_write_flow(self):
        status, sp = self.req("/api/sprints", {"title": "Sprint 1", "status": "in-progress", "author": "mark"})
        self.assertEqual((status, sp["id"]), (201, "S-001"))
        status, t = self.req("/api/tasks", {"title": "From the browser", "sprint": "S-001", "labels": "ui, web", "author": "mark"})
        self.assertEqual((status, t["id"], t["labels"]), (201, "T-001", ["ui", "web"]))
        status, t = self.req("/api/tasks/T-001/status", {"status": "in-progress", "author": "mark"})
        self.assertEqual((status, t["status"]), (200, "in-progress"))
        status, t = self.req("/api/tasks/T-001/assign", {"assignee": "mark", "author": "mark"})
        self.assertEqual(t["assignee"], "mark")
        status, err = self.req("/api/tasks/T-001/assign", {"assignee": "someone", "author": "other"})
        self.assertEqual(status, 400)
        status, t = self.req("/api/tasks/T-001/assign", {"assignee": "someone", "author": "other", "force": True})
        self.assertEqual(t["assignee"], "someone")
        status, t = self.req("/api/tasks/T-001/assign", {"assignee": "mark", "author": "mark", "force": True})
        status, t3 = self.req("/api/tasks", {"title": "Startable", "author": "mark"})
        status, t3 = self.req(f"/api/tasks/{t3['id']}/start", {"author": "mark"})
        self.assertEqual((status, t3["status"], t3["assignee"]), (200, "in-progress", "mark"))
        status, t = self.req("/api/tasks/T-001/log", {"message": "looked at it", "author": "mark"})
        self.assertEqual(t["worklog"][-1]["text"], "looked at it")
        self.assertEqual(t["worklog"][-1]["author"], "mark")
        status, t = self.req("/api/tasks/T-001/edit", {"priority": "high", "labels": ["x"]})
        self.assertEqual((t["priority"], t["labels"]), ("high", ["x"]))
        status, t2 = self.req("/api/tasks", {"title": "Blocked", "blocked_by": ["T-001"]})
        self.assertEqual(status, 201)
        bid = t2["id"]
        status, t2 = self.req(f"/api/tasks/{bid}/edit", {"blocked_by": "T-001"})
        self.assertEqual((t2["blocked_by"], t2["blocked"]), (["T-001"], True))
        status, err = self.req(f"/api/tasks/{bid}/status", {"status": "in-progress"})
        self.assertEqual(status, 400)
        status, t2 = self.req(f"/api/tasks/{bid}/status", {"status": "in-progress", "force": True})
        self.assertEqual(t2["status"], "in-progress")
        status, s = self.req("/api/sprints/S-001/status", {"status": "done"})
        self.assertEqual(s["status"], "done")
        status, c = self.req("/api/check/fix", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.board.get_task("T-001").status, "in-progress")

    def test_errors(self):
        self.assertEqual(self.req("/api/tasks", {"title": ""})[0], 400)
        self.assertEqual(self.req("/api/tasks/T-9/status", {"status": "done"})[0], 404)
        self.board.create_task("x")
        self.assertEqual(self.req("/api/tasks/T-1/status", {"status": "bogus"})[0], 400)
        self.assertEqual(self.req("/api/tasks/T-1/nope", {})[0], 404)
        self.assertEqual(self.req("/api/whatever", {})[0], 404)
        url = f"http://127.0.0.1:{self.port}/api/tasks"
        r = urllib.request.Request(url, data=b"not json", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 400)

    def test_read_only(self):
        ro = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board, read_only=True))
        port = ro.server_address[1]
        threading.Thread(target=ro.serve_forever, daemon=True).start()
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{port}/api/tasks", data=b'{"title": "x"}', headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(r)
            self.assertEqual(cm.exception.code, 400)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/board") as resp:
                self.assertTrue(json.loads(resp.read())["read_only"])
        finally:
            ro.shutdown()
            ro.server_close()


if __name__ == "__main__":
    unittest.main()
