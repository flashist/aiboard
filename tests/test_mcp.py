import json
import shutil
import tempfile
import unittest
from pathlib import Path

from aiboard.mcp import Server
from aiboard.store import Board


class McpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.board = Board(self.tmp)
        self.board.init(name="Demo")
        self.server = Server(self.board, author="bot")
        self._id = 0

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        return self.server.handle(msg)

    def call(self, name, **arguments):
        reply = self.rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertNotIn("error", reply, reply)
        return reply["result"]

    def test_handshake_and_tool_list(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(init["result"]["serverInfo"]["name"], "aiboard")
        self.assertIn("Demo", init["result"]["instructions"])
        self.assertIsNone(self.rpc("notifications/initialized", notify=True))
        self.assertEqual(self.rpc("ping")["result"], {})
        tools = self.rpc("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertTrue({"list_tasks", "get_task", "create_task", "change_task_status", "assign_task", "log_work",
                         "list_sprints", "get_sprint", "create_sprint", "check_board"} <= names)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_full_loop_through_tools(self):
        sp = self.call("create_sprint", title="Sprint 1", status="in-progress")["structuredContent"]
        self.assertEqual(sp["id"], "S-001")
        task = self.call("create_task", title="Do it", sprint="S-001", priority="high", labels=["a"])["structuredContent"]
        self.assertEqual((task["id"], task["status"], task["sprint"]), ("T-001", "backlog", "S-001"))
        self.call("assign_task", id="T-1", assignee="bot")
        self.call("change_task_status", id="T-001", status="wip")
        self.call("log_work", id="1", message="half way")
        done = self.call("change_task_status", id="T-001", status="done", note="shipped")["structuredContent"]
        self.assertEqual(done["status"], "done")
        full = self.call("get_task", id="T-001")["structuredContent"]
        self.assertEqual([e["author"] for e in full["worklog"]], ["bot"] * 5)
        self.assertIn("half way", full["worklog"][3]["text"])
        sprint = self.call("get_sprint", id="S-001")["structuredContent"]
        self.assertEqual(sprint["progress"]["percent"], 100)
        listed = self.call("list_tasks", status="done")["structuredContent"]["result"]
        self.assertEqual([t["id"] for t in listed], ["T-001"])
        self.assertTrue(self.call("check_board")["structuredContent"]["ok"])
        summary = self.call("board_summary")["structuredContent"]
        self.assertEqual(summary["task_counts"]["done"], 1)
        # text content is JSON too, for clients that ignore structuredContent
        self.assertEqual(json.loads(self.call("get_task", id="T-001")["content"][0]["text"])["id"], "T-001")

    def test_dependencies(self):
        self.call("create_task", title="A")
        b = self.call("create_task", title="B", blocked_by=["T-001"])["structuredContent"]
        self.assertTrue(b["blocked"])
        ready = self.call("list_tasks", unblocked=True)["structuredContent"]["result"]
        self.assertEqual([t["id"] for t in ready], ["T-001"])
        r = self.rpc("tools/call", {"name": "change_task_status", "arguments": {"id": "T-002", "status": "in-progress"}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("blocked", r["content"][0]["text"])
        self.call("unblock_task", id="T-002", blockers=["T-001"])
        self.assertFalse(self.call("get_task", id="T-002")["structuredContent"]["blocked"])
        self.assertEqual(self.call("block_task", id="T-002", blockers=["T-001"])["structuredContent"]["blocked_by"], ["T-001"])
        self.assertEqual(self.call("edit_task", id="T-002", blocked_by=[])["structuredContent"]["blocked_by"], [])

    def test_start_task(self):
        self.call("create_task", title="A")
        t = self.call("start_task", id="T-001")["structuredContent"]
        self.assertEqual((t["status"], t["assignee"]), ("in-progress", "bot"))
        other = Server(self.board, author="other")
        r = other.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "start_task", "arguments": {"id": "T-001"}}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("not in backlog", r["content"][0]["text"])
        r = other.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "assign_task", "arguments": {"id": "T-001", "assignee": "other"}}})["result"]
        self.assertTrue(r["isError"])

    def test_errors(self):
        r = self.rpc("tools/call", {"name": "get_task", "arguments": {"id": "T-404"}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("not found", r["content"][0]["text"])
        r = self.rpc("tools/call", {"name": "change_task_status", "arguments": {"id": "T-1", "status": "bogus"}})["result"]
        self.assertTrue(r["isError"])
        r = self.rpc("tools/call", {"name": "no_such_tool", "arguments": {}})["result"]
        self.assertTrue(r["isError"])
        self.assertEqual(self.rpc("does/not/exist")["error"]["code"], -32601)
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "id": 9, "result": {}}))  # stray response

    def test_stdio_framing(self):
        import io

        inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n\nnot json\n")
        out = io.StringIO()
        self.server.serve_stdio(inp, out)
        lines = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["result"], {})
        self.assertEqual(lines[1]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
