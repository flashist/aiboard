---
id: T-021
title: "Snapshot is O(n^2): sprint_progress re-scans the tree per task"
sprint: S-003
priority: high
assignee: 
labels: []
blocked_by: []
created: "2026-09-18T09:29:57Z"
updated: "2026-09-18T09:29:57Z"
---

# Snapshot is O(n^2): sprint_progress re-scans the tree per task

Measured 2026-09-18 against a synthetic board built from fkit's real task content
(404 briefs, median brief 9.2 KB, median worklog 16.9 KB).

`Board.snapshot()` — what `/api/board` returns and the web UI polls **every 3 seconds** —
is quadratic in task count:

| tasks | snapshot | list_tasks |
|------:|---------:|-----------:|
|   100 |    70 ms |      21 ms |
|   234 |   232 ms |      48 ms |
|   405 |   611 ms |      89 ms |
|   800 |  2186 ms |     177 ms |

8x the tasks costs 31x the time. `list_tasks()` is linear, so the whole curve comes from
`sprint_progress()`.

Cause (cProfile, n=234): `sprint_progress` is 0.288s of a 0.360s snapshot. It calls
`get_task(tid)` once per sprint member; each `get_task` goes through `_locate` -> `_scan`,
which walks all four status directories. One snapshot calls `_scan` **28,543 times** and
loads 471 tasks to describe 234.

Fix: build the task index once and have `sprint_progress` read from it instead of calling
`get_task` per member. Prototyped as a monkeypatch (repo untouched) and verified to produce
identical task ids and identical per-status counts:

| tasks | current | indexed | speedup |
|------:|--------:|--------:|--------:|
|   100 |   72 ms |   22 ms |    3.3x |
|   234 |  236 ms |   50 ms |    4.7x |
|   405 |  616 ms |   90 ms |    6.8x |
|   800 | 2185 ms |  181 ms |   12.1x |

The curve becomes linear.

Acceptance criteria:
- `snapshot()` performs one tree scan, not one per sprint member.
- `sprint_progress()` accepts an optional prewarmed index; behaviour unchanged when absent.
- A test asserts snapshot cost grows linearly (or simply that `_scan` is called O(1) times per snapshot).
- Existing 41 tests stay green.

Why it matters now: this is architectural, not a Python artifact. A Node port that copies the
current design inherits the same quadratic curve and just moves the cliff. Fix here first, then
port the fixed design.
