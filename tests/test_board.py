import json
import shutil
import tempfile
import unittest
from pathlib import Path

from aiboard.cli import main
from aiboard.model import dump_front_matter, normalize_id, normalize_status, parse_front_matter, parse_worklog, slugify
from aiboard.store import Board, BoardError, NotFound, find_board_root


class ModelTests(unittest.TestCase):
    def test_front_matter_roundtrip(self):
        meta = {"id": "T-001", "title": "Fix: login [urgent]", "sprint": None, "labels": ["a", "b"], "n": 3, "ok": True}
        text = dump_front_matter(meta, "# Fix\n\nbody")
        parsed, body = parse_front_matter(text)
        self.assertEqual(parsed, meta)
        self.assertEqual(body.strip(), "# Fix\n\nbody")

    def test_front_matter_inline_list_and_missing(self):
        meta, body = parse_front_matter("---\ntasks: [T-001, T-002]\nempty:\n---\nhello")
        self.assertEqual(meta["tasks"], ["T-001", "T-002"])
        self.assertIsNone(meta["empty"])
        self.assertEqual(body, "hello")
        self.assertEqual(parse_front_matter("no front matter"), ({}, "no front matter"))

    def test_quotes_and_backslashes_roundtrip(self):
        for title in ('Fix "quoted" bug: x', "back\\slash", 'both \\"', "it's"):
            meta, _ = parse_front_matter(dump_front_matter({"title": title}, ""))
            self.assertEqual(meta["title"], title)

    def test_ids_and_statuses(self):
        for ref in ("1", "T-1", "t-001", "T-001-some-slug"):
            self.assertEqual(normalize_id("T", ref), "T-001")
        self.assertIsNone(normalize_id("T", "S-001"))
        self.assertEqual(normalize_status("In Progress"), "in-progress")
        self.assertEqual(normalize_status("wip"), "in-progress")
        with self.assertRaises(ValueError):
            normalize_status("bogus")
        self.assertEqual(slugify("Hello, World!  Ünicode"), "hello-world-nicode")

    def test_worklog_parse(self):
        entries = parse_worklog("# Worklog\n\n## 2026-01-01T00:00:00Z — alice\n\nfirst\n\n## 2026-01-02T00:00:00Z - bob\n\nsecond\nline\n")
        self.assertEqual([(e.author, e.text) for e in entries], [("alice", "first"), ("bob", "second\nline")])


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.board = Board(self.tmp)
        self.board.init()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_requires_init(self):
        with self.assertRaises(BoardError):
            Board(self.tmp / "nope").list_tasks()

    def test_create_and_move_task(self):
        t = self.board.create_task("Fix login", body="Details", priority="high", assignee="claude")
        self.assertEqual(t.id, "T-001")
        self.assertEqual(t.status, "backlog")
        self.assertTrue((self.tmp / "tasks/backlog/T-001-fix-login/brief.md").exists())
        self.assertTrue((self.tmp / "tasks/backlog/T-001-fix-login/worklog.md").exists())

        t = self.board.move_task("T-001", "in progress", author="claude")
        self.assertEqual(t.status, "in-progress")
        self.assertTrue((self.tmp / "tasks/in-progress/T-001-fix-login").is_dir())
        self.assertFalse((self.tmp / "tasks/backlog/T-001-fix-login").exists())
        self.assertIn("backlog", t.worklog[-1].text)
        self.assertEqual(t.worklog[-1].author, "claude")

        t2 = self.board.create_task("Second")
        self.assertEqual(t2.id, "T-002")
        self.assertEqual([x.id for x in self.board.list_tasks(status="backlog")], ["T-002"])

    def test_log_appends(self):
        self.board.create_task("A")
        self.board.log("1", "did a thing", author="bot")
        t = self.board.get_task("T-001")
        self.assertEqual(t.worklog[-1].text, "did a thing")
        self.assertEqual(len(t.worklog), 2)

    def test_sprint_membership_stays_in_sync(self):
        s = self.board.create_sprint("Sprint 1", goal="Ship", start="2026-09-14", end="2026-09-25")
        self.assertEqual(s.id, "S-001")
        t1 = self.board.create_task("One", sprint="S-001")
        t2 = self.board.create_task("Two")
        self.board.sprint_add("S-001", ["T-002"])
        s = self.board.get_sprint("1")
        self.assertEqual(s.tasks, ["T-001", "T-002"])
        self.assertEqual(self.board.get_task("T-002").sprint, "S-001")
        text = (s.path / "sprint.md").read_text()
        self.assertIn("- [ ] T-001 — One (backlog)", text)

        self.board.move_task("T-001", "done")
        text = (s.path / "sprint.md").read_text()
        self.assertIn("- [x] T-001 — One (done)", text)
        p = self.board.sprint_progress(self.board.get_sprint("S-001"))
        self.assertEqual((p["total"], p["counts"]["done"], p["percent"]), (2, 1, 50))

        self.board.sprint_remove("S-001", ["T-002"])
        self.assertEqual(self.board.get_sprint("S-001").tasks, ["T-001"])
        self.assertIsNone(self.board.get_task("T-002").sprint)

        s2 = self.board.create_sprint("Sprint 2")
        self.board.update_task("T-001", sprint="S-002")
        self.assertEqual(self.board.get_sprint("S-001").tasks, [])
        self.assertEqual(self.board.get_sprint("S-002").tasks, ["T-001"])
        self.assertEqual(self.board.check(), [])

    def test_move_sprint(self):
        self.board.create_sprint("Sprint 1")
        s = self.board.move_sprint("S-001", "in-progress")
        self.assertTrue((self.tmp / "sprints/in-progress/S-001-sprint-1/sprint.md").exists())
        self.assertEqual(s.status, "in-progress")

    def test_check_detects_inconsistency(self):
        self.board.create_sprint("Sprint 1")
        self.board.create_task("One", sprint="S-001")
        brief = self.tmp / "tasks/backlog/T-001-one/brief.md"
        brief.write_text(brief.read_text().replace("sprint: S-001", "sprint: S-009"))
        problems = self.board.check()
        self.assertTrue(any("missing sprint S-009" in p for p in problems))
        self.assertTrue(any("says sprint=S-009" in p for p in problems))

    def test_quoted_title_survives_store(self):
        t = self.board.create_task('Fix "quoted" bug: x')
        self.assertEqual(self.board.get_task(t.id).title, 'Fix "quoted" bug: x')

    def test_check_detects_and_fixes_stale_sprint_list(self):
        self.board.create_sprint("Sprint 1")
        t = self.board.create_task("One", sprint="S-001")
        # move by hand, as the README allows
        shutil.move(str(t.path), str(self.tmp / "tasks/done" / t.path.name))
        problems = self.board.check()
        self.assertTrue(any("stale" in p and "S-001" in p for p in problems), problems)
        problems = self.board.check(fix=True)
        self.assertTrue(any(p.endswith("(fixed)") for p in problems), problems)
        self.assertEqual(self.board.check(), [])
        self.assertIn("- [x] T-001 — One (done)", (self.board.get_sprint("S-001").path / "sprint.md").read_text())

    def test_hand_made_task_is_readable(self):
        """Agents may create folders by hand; the tool must still read them."""
        d = self.tmp / "tasks/in-progress/T-007-hand-made"
        d.mkdir()
        (d / "brief.md").write_text("# Hand made task\n\nJust a heading, no front matter.\n")
        t = self.board.get_task("T-007")
        self.assertEqual((t.title, t.status), ("Hand made task", "in-progress"))
        self.assertEqual(self.board.create_task("Next").id, "T-008")
        self.assertIn("T-007 has no worklog.md", self.board.check())

    def test_assign_logs(self):
        self.board.create_task("A")
        t = self.board.assign("T-001", "agent-7", author="agent-7")
        self.assertEqual(t.assignee, "agent-7")
        self.assertIn("agent-7", t.worklog[-1].text)
        t = self.board.assign("T-001", None)
        self.assertIsNone(t.assignee)
        self.assertIn("nobody", t.worklog[-1].text)

    def test_parallel_writers_get_unique_ids(self):
        """20 processes create tasks and move them at once; ids stay unique, board stays consistent."""
        import os
        import subprocess
        import sys

        self.board.create_sprint("S")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        procs = [
            subprocess.Popen([sys.executable, "-m", "aiboard", "--root", str(self.tmp), "task", "new", f"job {i}", "--sprint", "S-001"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for i in range(20)
        ]
        errors = [pr.communicate()[1].decode() for pr in procs if pr.wait() != 0]
        self.assertEqual(errors, [])
        ids = [t.id for t in self.board.list_tasks()]
        self.assertEqual(ids, [f"T-{i:03d}" for i in range(1, 21)])
        procs = [
            subprocess.Popen([sys.executable, "-m", "aiboard", "--root", str(self.tmp), "task", "change-status", f"T-{i:03d}", "done"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for i in range(1, 21)
        ]
        errors = [pr.communicate()[1].decode() for pr in procs if pr.wait() != 0]
        self.assertEqual(errors, [])
        self.assertEqual(self.board.check(), [])
        self.assertEqual(self.board.sprint_progress(self.board.get_sprint("S-001"))["percent"], 100)

    def test_init_writes_config_and_discovery_walks_up(self):
        import os

        self.assertEqual(self.board.name, self.tmp.name)  # no config written by setUp's init? it is:
        cfg = self.tmp / "aiboard.json"
        self.assertTrue(cfg.exists())
        cfg.write_text('{"name": "My Product", "version": 1}')
        self.assertEqual(Board(self.tmp).name, "My Product")
        deep = self.tmp / "src" / "pkg"
        deep.mkdir(parents=True)
        self.assertEqual(find_board_root(deep), self.tmp)
        self.assertIsNone(find_board_root(Path(tempfile.mkdtemp())))
        cwd = os.getcwd()
        try:
            os.chdir(deep)
            self.assertEqual(Board.locate(None).root, self.tmp)
            os.environ["AIBOARD_ROOT"] = str(self.tmp / "elsewhere")
            self.assertEqual(Board.locate(None).root, (self.tmp / "elsewhere").resolve())
        finally:
            os.environ.pop("AIBOARD_ROOT", None)
            os.chdir(cwd)
        self.assertEqual(Board.locate(str(self.tmp)).root, self.tmp)
        self.assertEqual(self.board.snapshot()["name"], "My Product")

    def test_not_found(self):
        with self.assertRaises(NotFound):
            self.board.get_task("T-042")

    def test_snapshot_shape(self):
        self.board.create_sprint("S")
        self.board.create_task("T", sprint="S-001")
        snap = self.board.snapshot()
        self.assertEqual(snap["statuses"], ["backlog", "in-progress", "done", "cancelled"])
        self.assertEqual(snap["sprints"][0]["progress"]["total"], 1)
        json.dumps(snap)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_cli(self, *argv):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--root", str(self.tmp), *argv])
        return code, out.getvalue()

    def test_full_flow(self):
        self.assertEqual(self.run_cli("init")[0], 0)
        code, out = self.run_cli("--json", "sprint", "new", "Sprint 1", "--status", "in-progress")
        self.assertEqual(json.loads(out)["id"], "S-001")
        code, out = self.run_cli("--json", "task", "new", "Do it", "--sprint", "S-001", "--by", "agent-1")
        self.assertEqual(json.loads(out)["status"], "backlog")
        self.assertEqual(self.run_cli("task", "move", "T-001", "done", "--note", "shipped")[0], 0)
        code, out = self.run_cli("--json", "sprint", "show", "S-001")
        data = json.loads(out)
        self.assertEqual(data["progress"]["percent"], 100)
        code, out = self.run_cli("board")
        self.assertIn("T-001 Do it", out)
        self.assertEqual(self.run_cli("check")[0], 0)
        code, out = self.run_cli("--json", "task", "show", "T-999")
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))
        code, out = self.run_cli("--json", "task", "change-status", "T-001", "kinda-done")
        self.assertEqual(code, 1)
        self.assertIn("unknown status", json.loads(out)["error"])
        self.assertEqual(self.run_cli("task", "new", "Alias status", "--status", "wip")[0], 0)
        self.assertEqual(self.run_cli("--json", "task", "list", "--status", "in progress")[0], 0)

    def test_argparse_errors_are_json(self):
        import contextlib
        import io
        import sys as _sys

        self.run_cli("init")
        out = io.StringIO()
        argv_backup = _sys.argv
        _sys.argv = ["aiboard", "--json", "task", "new", "x", "--priority", "urgent"]
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                main(["--root", str(self.tmp), "--json", "task", "new", "x", "--priority", "urgent"])
        finally:
            _sys.argv = argv_backup
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("invalid choice", json.loads(out.getvalue())["error"])

    def test_board_truncates_with_ellipsis(self):
        self.run_cli("init")
        self.run_cli("task", "new", "A very long title that certainly does not fit into the column")
        code, out = self.run_cli("board")
        self.assertIn("…", out)


if __name__ == "__main__":
    unittest.main()
