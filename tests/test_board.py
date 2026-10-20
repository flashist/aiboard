import json
import shutil
import tempfile
import unittest
from pathlib import Path

from aiboard.cli import main
from aiboard.model import (
    PRIORITIES,
    Task,
    dump_front_matter,
    normalize_id,
    normalize_priority,
    normalize_status,
    parse_front_matter,
    parse_worklog,
    slugify,
)
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

    def test_zero_padded_digits_roundtrip_byte_identical(self):
        text = "---\nid: 0013\nblocked_by:\n  - 0404\n  - 0013\n---\n\nbody\n"
        meta, body = parse_front_matter(text)
        self.assertEqual(meta, {"id": "0013", "blocked_by": ["0404", "0013"]})
        self.assertEqual(dump_front_matter(meta, body), text)

    def test_hostile_scalars_roundtrip(self):
        values = ["0404", "0013", "007", "404", "-3", "-0", 5, -3, 0, True, False, None,
                  "true", "False", "null", "None", "~", "", " pad ", "🚀 emoji", "a: b", "[x]",
                  "{y}", '"quoted"', "'single'", "back\\slash", 'both \\"', "# hash"]
        for v in values:
            meta = {"v": v, "l": [v, "ok"]}
            parsed, _ = parse_front_matter(dump_front_matter(meta, ""))
            self.assertEqual(parsed["v"], v, repr(v))
            self.assertIs(type(parsed["v"]), type(v), repr(v))
            # None and "" inside a block list are not preserved yet; that is outside this fix.
            if v not in ("", None):
                self.assertEqual(parsed["l"], [v, "ok"], repr(v))

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

    def test_priorities(self):
        self.assertEqual(PRIORITIES, ["low", "medium", "high", "critical"])
        self.assertEqual(normalize_priority("critical"), "critical")
        for alias in ("urgent", "URGENT", " Urgent "):
            self.assertEqual(normalize_priority(alias), "critical")
        self.assertEqual(normalize_priority("High"), "high")
        with self.assertRaises(ValueError):
            normalize_priority("bogus")

    def test_rank_defaults_to_id_order(self):
        task = lambda meta: Task(id="T-007", title="x", status="backlog", path=Path("."), meta=meta)
        self.assertEqual(task({}).rank, 7000)  # boards from before ranking keep their id order
        self.assertEqual(task({"rank": 2500}).rank, 2500)
        self.assertEqual(task({"rank": "-1500"}).rank, -1500)  # survives a string round trip too
        self.assertEqual(task({"rank": "soon"}).rank, 7000)  # malformed: falls back, `check` reports it

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

    def test_critical_priority_and_urgent_alias(self):
        t = self.board.create_task("Prod is down", priority="critical")
        self.assertEqual(t.priority, "critical")
        # The alias is resolved before writing, so the file only ever holds the canonical name.
        t = self.board.create_task("Checkout broken", priority="urgent")
        self.assertEqual(t.priority, "critical")
        self.assertIn("priority: critical", (t.path / "brief.md").read_text(encoding="utf-8"))
        t = self.board.update_task(t.id, priority="high")
        self.assertEqual(t.priority, "high")
        t = self.board.update_task(t.id, priority="Urgent")
        self.assertEqual(t.priority, "critical")
        with self.assertRaises(BoardError):
            self.board.create_task("x", priority="bogus")
        with self.assertRaises(BoardError):
            self.board.update_task(t.id, priority="bogus")

    def test_hand_edited_urgent_reads_as_critical(self):
        t = self.board.create_task("Hand edited")
        brief = t.path / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace("priority: medium", "priority: urgent"), encoding="utf-8")
        self.assertEqual(self.board.get_task(t.id).priority, "critical")

    def order(self, **filters):
        return [t.id for t in self.board.list_tasks(**filters)]

    def test_new_tasks_join_the_bottom_and_critical_ones_the_top(self):
        for title in ("a", "b", "c"):
            self.board.create_task(title)
        self.assertEqual(self.order(), ["T-001", "T-002", "T-003"])
        self.board.create_task("prod is down", priority="critical")
        self.board.create_task("d")
        self.assertEqual(self.order(), ["T-004", "T-001", "T-002", "T-003", "T-005"])

    def test_rank_top_bottom_before_after(self):
        for title in ("a", "b", "c", "d"):
            self.board.create_task(title)
        self.board.rank_task("T-4", top=True)
        self.assertEqual(self.order(), ["T-004", "T-001", "T-002", "T-003"])
        self.board.rank_task("T-1", bottom=True)
        self.assertEqual(self.order(), ["T-004", "T-002", "T-003", "T-001"])
        self.board.rank_task("T-1", before="T-2")
        self.assertEqual(self.order(), ["T-004", "T-001", "T-002", "T-003"])
        self.board.rank_task("T-4", after="T-3")
        self.assertEqual(self.order(), ["T-001", "T-002", "T-003", "T-004"])

    def test_a_move_rewrites_only_the_moved_task(self):
        for title in ("a", "b", "c", "d"):
            self.board.create_task(title)
        briefs = {t.id: (t.path / "brief.md").read_text(encoding="utf-8") for t in self.board.list_tasks()}
        self.board.rank_task("T-004", after="T-001")
        changed = [t.id for t in self.board.list_tasks() if (t.path / "brief.md").read_text(encoding="utf-8") != briefs[t.id]]
        self.assertEqual(changed, ["T-004"])
        self.assertEqual(self.board.get_task("T-004").rank, 1500)
        # Ranking a task to where it already is writes nothing at all.
        before = (self.board.get_task("T-004").path / "brief.md").read_text(encoding="utf-8")
        self.board.rank_task("T-004", after="T-001")
        self.assertEqual((self.board.get_task("T-004").path / "brief.md").read_text(encoding="utf-8"), before)

    def test_rank_respaces_when_a_gap_runs_out(self):
        self.board.create_task("first")
        self.board.create_task("last")
        inserted = []
        for i in range(15):  # 1000 halves to nothing after about ten inserts at the same spot
            t = self.board.create_task(f"filler {i}")
            self.board.rank_task(t.id, after="T-001")
            inserted.append(t.id)
        expected = ["T-001"] + list(reversed(inserted)) + ["T-002"]
        self.assertEqual(self.order(), expected)
        ranks = [t.rank for t in self.board.list_tasks()]
        self.assertEqual(len(set(ranks)), len(ranks))

    def test_rank_validation(self):
        self.board.create_task("a")
        self.board.create_task("b")
        with self.assertRaises(BoardError):
            self.board.rank_task("T-001")
        with self.assertRaises(BoardError):
            self.board.rank_task("T-001", top=True, before="T-002")
        with self.assertRaises(BoardError):
            self.board.rank_task("T-001", before="T-001")
        with self.assertRaises(NotFound):
            self.board.rank_task("T-001", after="T-099")

    def test_rank_survives_status_changes_and_orders_every_view(self):
        sp = self.board.create_sprint("Sprint 1")
        for title in ("a", "b", "c"):
            self.board.create_task(title, sprint=sp.id)
        self.board.rank_task("T-003", top=True)
        self.board.move_task("T-003", "in-progress")
        self.board.move_task("T-001", "in-progress")
        self.assertEqual(self.order(status="in-progress"), ["T-003", "T-001"])
        progress = self.board.sprint_progress(self.board.get_sprint(sp.id))
        self.assertEqual([t.id for t in progress["tasks"]], ["T-003", "T-001", "T-002"])

    def test_raising_to_critical_moves_to_the_top_once(self):
        for title in ("a", "b", "c"):
            self.board.create_task(title)
        self.board.update_task("T-003", priority="critical")
        self.assertEqual(self.order()[0], "T-003")
        self.board.rank_task("T-003", bottom=True)
        # Already critical: other edits leave the manual order alone.
        self.board.update_task("T-003", priority="critical", title="still critical")
        self.assertEqual(self.order()[-1], "T-003")

    def test_ranking_does_not_touch_updated(self):
        # `updated` feeds stale detection; reordering is not work on the task.
        self.board.create_task("a")
        t = self.board.create_task("b")
        brief = t.path / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace(t.meta["updated"], "2020-01-01T00:00:00Z"), encoding="utf-8")
        self.board.rank_task("T-002", top=True)
        self.assertEqual(self.board.get_task("T-002").meta["updated"], "2020-01-01T00:00:00Z")

    def test_boards_without_ranks_keep_id_order(self):
        for title in ("a", "b", "c"):
            t = self.board.create_task(title)
            brief = t.path / "brief.md"
            brief.write_text("".join(l for l in brief.read_text(encoding="utf-8").splitlines(True) if not l.startswith("rank:")),
                             encoding="utf-8")
        self.assertEqual(self.order(), ["T-001", "T-002", "T-003"])
        self.board.rank_task("T-003", before="T-002")
        self.assertEqual(self.order(), ["T-001", "T-003", "T-002"])
        self.assertEqual(self.board.get_task("T-003").rank, 1500)
        self.assertIsNone(self.board.get_task("T-002").meta.get("rank"))  # neighbours untouched

    def test_check_reports_a_malformed_rank(self):
        t = self.board.create_task("a")
        brief = t.path / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace("rank: 1000", "rank: soon"), encoding="utf-8")
        self.assertTrue(any("rank" in p and "T-001" in p for p in self.board.check()))

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

    def test_pointer_file_discovery(self):
        project = Path(tempfile.mkdtemp()).resolve()
        try:
            board = Board(project / "board")
            board.init(name="Sub")
            self.assertIsNotNone(board.write_pointer(project))
            self.assertIsNone(board.write_pointer(project))  # idempotent
            self.assertEqual(json.loads((project / "aiboard.json").read_text()), {"board": "board"})
            deep = project / "src" / "x"
            deep.mkdir(parents=True)
            self.assertEqual(find_board_root(deep), project / "board")
            self.assertEqual(Board(find_board_root(deep)).name, "Sub")
        finally:
            shutil.rmtree(project)

    def test_dependencies(self):
        a = self.board.create_task("A")
        b = self.board.create_task("B", blocked_by=["T-1"])
        self.assertEqual(b.blocked_by, ["T-001"])
        self.assertTrue(b.blocked)
        self.assertEqual(b.blockers[0]["status"], "backlog")
        self.assertEqual([t.id for t in self.board.list_tasks(unblocked=True)], ["T-001"])
        with self.assertRaises(BoardError) as cm:
            self.board.move_task("T-002", "in-progress")
        self.assertIn("blocked by T-001", str(cm.exception))
        self.board.move_task("T-002", "in-progress", force=True)
        self.assertIn("in progress but blocked", " ".join(self.board.check()))
        self.board.move_task("T-002", "backlog", force=True)
        self.board.move_task("T-001", "done")
        b = self.board.get_task("T-002")
        self.assertFalse(b.blocked)
        self.board.move_task("T-002", "in-progress")  # allowed now
        # block / unblock keep a worklog trail and validate ids
        c = self.board.create_task("C")
        c = self.board.block("T-003", ["T-002", "2"], author="x")
        self.assertEqual(c.blocked_by, ["T-002"])
        self.assertIn("Blocked by T-002", c.worklog[-1].text)
        with self.assertRaises(BoardError):
            self.board.block("T-003", ["T-003"])
        with self.assertRaises(NotFound):
            self.board.block("T-003", ["T-999"])
        c = self.board.unblock("T-003", ["T-002"])
        self.assertEqual(c.blocked_by, [])
        self.assertIn("No longer blocked", c.worklog[-1].text)
        # cycles and dangling references are reported by check
        self.board.update_task("T-001", blocked_by=["T-003"])
        self.board.update_task("T-003", blocked_by=["T-001"])
        problems = self.board.check()
        self.assertTrue(any("dependency cycle" in p and "T-001" in p for p in problems), problems)
        brief = self.board.get_task("T-003").path / "brief.md"
        brief.write_text(brief.read_text().replace("- T-001", "- T-777"))
        self.assertTrue(any("missing task T-777" in p for p in self.board.check()))
        self.assertEqual(self.board.get_task("T-003").blockers[0]["status"], "missing")

    def test_start_is_an_atomic_claim(self):
        self.board.create_task("A")
        t = self.board.start("T-001", "agent-1", author="agent-1")
        self.assertEqual((t.status, t.assignee), ("in-progress", "agent-1"))
        self.assertEqual([e.author for e in t.worklog][-2:], ["agent-1", "agent-1"])
        with self.assertRaises(BoardError) as cm:
            self.board.start("T-001", "agent-2", author="agent-2")
        self.assertIn("in-progress by agent-1", str(cm.exception))
        self.board.create_task("B", assignee="agent-1")
        with self.assertRaises(BoardError) as cm:
            self.board.start("T-002", "agent-2", author="agent-2")
        self.assertIn("already assigned to agent-1", str(cm.exception))
        self.assertEqual(self.board.start("T-002", "agent-1", author="agent-1").status, "in-progress")  # own task: fine
        self.board.create_task("C", blocked_by=["T-001"])
        with self.assertRaises(BoardError):
            self.board.start("T-003", "agent-2")
        self.assertEqual(self.board.start("T-003", "agent-2", force=True).assignee, "agent-2")

    def test_assign_refuses_to_steal_without_force(self):
        self.board.create_task("A")
        self.board.assign("T-001", "agent-1", author="agent-1")
        with self.assertRaises(BoardError) as cm:
            self.board.assign("T-001", "agent-2", author="agent-2")
        self.assertIn("already assigned to agent-1", str(cm.exception))
        self.assertEqual(self.board.assign("T-001", "agent-2", author="agent-1").assignee, "agent-2")  # holder hands over
        self.assertIsNone(self.board.assign("T-001", None, author="someone").assignee)  # unassign is always allowed
        self.board.assign("T-001", "agent-3", author="agent-3")
        self.assertEqual(self.board.assign("T-001", "boss", author="boss", force=True).assignee, "boss")

    def test_parallel_start_has_exactly_one_winner(self):
        import os
        import subprocess
        import sys

        self.board.create_task("Contended")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        procs = [
            subprocess.Popen([sys.executable, "-m", "aiboard", "--root", str(self.tmp), "task", "start", "T-001", "--as", f"agent-{i}", "--by", f"agent-{i}"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for i in range(12)
        ]
        results = [pr.wait() for pr in procs]
        self.assertEqual(results.count(0), 1, results)
        t = self.board.get_task("T-001")
        self.assertEqual(t.status, "in-progress")
        self.assertTrue(t.assignee.startswith("agent-"))
        self.assertEqual([e.text for e in t.worklog].count("Status changed `backlog` → `in-progress`."), 1)

    def test_comments_and_stale_detection(self):
        self.board.create_task("A")
        t = self.board.comment("T-001", "Is this still needed?", author="mark")
        self.assertEqual(len(t.comments), 1)
        self.assertEqual((t.comments[0].author, t.comments[0].text), ("mark", "Is this still needed?"))
        self.assertTrue((t.path / "comments.md").exists())
        self.assertEqual(len(t.worklog), 1)  # comments do not pollute the worklog
        self.assertEqual(t.to_dict()["comments_count"], 1)
        self.assertEqual(t.to_dict(include_body=True)["comments"][0]["author"], "mark")
        self.assertFalse(t.stale)  # backlog tasks are never stale

        t = self.board.start("T-001", "bot")
        self.assertFalse(t.stale)
        # age every timestamp by a month
        for name in ("worklog.md", "comments.md", "brief.md"):
            f = t.path / name
            f.write_text(f.read_text().replace("2026-09-", "2026-08-"))
        t = self.board.get_task("T-001")
        self.assertTrue(t.stale)
        self.assertEqual([x.id for x in self.board.list_tasks(stale=True)], ["T-001"])
        self.assertTrue(any("no worklog entry or comment since" in p for p in self.board.check()))
        (self.tmp / "aiboard.json").write_text('{"name": "x", "stale_after_hours": 100000}')
        self.assertFalse(self.board.get_task("T-001").stale)
        (self.tmp / "aiboard.json").write_text('{"name": "x"}')
        self.board.comment("T-001", "still on it", author="bot")  # any activity clears it
        self.assertFalse(self.board.get_task("T-001").stale)
        # needs_reply: latest comment from someone other than the assignee
        self.assertFalse(self.board.get_task("T-001").needs_reply)
        self.board.comment("T-001", "how is it going?", author="mark")
        t = self.board.get_task("T-001")
        self.assertTrue(t.needs_reply)
        self.assertEqual(t.last_comment_by, "mark")
        self.assertEqual([x.id for x in self.board.list_tasks(needs_reply=True)], ["T-001"])
        self.board.comment("T-001", "nearly done", author="bot")
        self.assertFalse(self.board.get_task("T-001").needs_reply)

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


class InstructionsTests(unittest.TestCase):
    def test_install_block_is_idempotent(self):
        from aiboard.instructions import BEGIN, END, install_block

        d = Path(tempfile.mkdtemp())
        try:
            f = d / "AGENTS.md"
            self.assertEqual(install_block(f, "this repository"), "created")
            self.assertEqual(install_block(f, "this repository"), "unchanged")
            self.assertEqual(install_block(f, "`board/`"), "updated")
            text = f.read_text()
            self.assertEqual(text.count(BEGIN), 1)
            self.assertEqual(text.count(END), 1)
            self.assertIn("tracker in `board/`.", text)
            g = d / "CLAUDE.md"
            g.write_text("# Existing\n\nKeep me.\n")
            self.assertEqual(install_block(g, "this repository"), "updated")
            self.assertTrue(g.read_text().startswith("# Existing\n\nKeep me.\n"))
            self.assertIn("tracker in this repository.", g.read_text())
        finally:
            shutil.rmtree(d)

    def test_block_tells_agents_to_take_work_from_the_top(self):
        from aiboard.instructions import render_block

        block = render_block()
        self.assertIn("queue order", block)
        self.assertIn("aiboard task rank", block)


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
        self.assertIn("board: ", out)
        code, out = self.run_cli("--json", "info")
        info = json.loads(out)
        self.assertEqual((info["root"], info["task_counts"]["done"], info["active_sprints"]), (str(self.tmp), 1, ["S-001"]))
        self.assertEqual(self.run_cli("check")[0], 0)
        code, out = self.run_cli("task", "show", "T-001", "--json")  # --json anywhere
        self.assertEqual(json.loads(out)["id"], "T-001")
        code, out = self.run_cli("task", "new", "Startable", "--json")
        code, out = self.run_cli("task", "start", json.loads(out)["id"], "--as", "worker", "--json")
        self.assertEqual((code, json.loads(out)["status"], json.loads(out)["assignee"]), (0, "in-progress", "worker"))
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
        _sys.argv = ["aiboard", "--json", "task", "new", "x", "--priority", "bogus"]
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                main(["--root", str(self.tmp), "--json", "task", "new", "x", "--priority", "bogus"])
        finally:
            _sys.argv = argv_backup
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("invalid choice", json.loads(out.getvalue())["error"])

    def test_critical_priority_via_cli(self):
        self.run_cli("init")
        code, out = self.run_cli("--json", "task", "new", "Prod is down", "--priority", "urgent")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["priority"], "critical")
        self.run_cli("task", "new", "Minor")
        code, out = self.run_cli("--json", "task", "edit", "T-002", "--priority", "critical")
        self.assertEqual((code, json.loads(out)["priority"]), (0, "critical"))
        code, out = self.run_cli("task", "list")
        self.assertIn("critical", out)

    def test_task_rank_via_cli(self):
        self.run_cli("init")
        for title in ("a", "b", "c"):
            self.run_cli("task", "new", title)
        code, out = self.run_cli("task", "rank", "T-003", "--top")
        self.assertEqual(code, 0)
        self.assertIn("Ranked T-003 to the top", out)
        code, out = self.run_cli("--json", "task", "rank", "T-001", "--after", "T-002")
        self.assertEqual((code, json.loads(out)["id"]), (0, "T-001"))
        code, out = self.run_cli("--json", "task", "list")
        self.assertEqual([t["id"] for t in json.loads(out)], ["T-003", "T-002", "T-001"])
        code, out = self.run_cli("task", "show", "T-003")
        self.assertIn("rank:", out)

    def test_board_truncates_with_ellipsis(self):
        self.run_cli("init")
        self.run_cli("task", "new", "A very long title that certainly does not fit into the column")
        code, out = self.run_cli("board")
        self.assertIn("…", out)


if __name__ == "__main__":
    unittest.main()
