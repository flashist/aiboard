# Worklog

## 2026-09-29T20:51:42Z — aiboard-lead

Task created in `backlog`.

## 2026-09-29T20:51:42Z — aiboard-lead

Assigned to `aiboard-lead`.

## 2026-09-29T20:51:42Z — aiboard-lead

Status changed `backlog` → `in-progress`.

## 2026-09-29T20:58:56Z — aiboard-lead

Implemented as designed in the brief.

- model: `RANK_STEP = 1000`; `Task.rank` (stored int, else id x 1000; a malformed value falls
  back and `check` reports it; int() on the string, so it survives the T-023 fix either way).
  `rank` is in every task's JSON.
- store: `list_tasks` and `sprint_progress` sort by (rank, id). New tasks go to the bottom,
  critical ones to the top; `update_task` ranks a task to the top when it becomes critical
  (not on later edits). `rank_task(top|bottom|before|after)` takes the midpoint of its
  neighbours and writes one file, writes nothing when the task is already there, and respaces
  the whole order only when a gap runs out. Rank writes leave `updated` alone, so reordering
  never makes a stale task look active.
- CLI `task rank`, MCP `rank_task`, HTTP `POST /api/tasks/<id>/rank`; `task show` prints rank.
- web: drop position decides the rank (a line shows where the card lands); dropping in another
  column changes status then ranks it; drawer shows "#n of m in <status>" with Move to top /
  Move to bottom.
- Fixed: mutate() re-rendered through load(), which T-018 holds while the pointer is on the
  board, so the user's own drop could wait until the mouse left. Own changes now render at
  once; changes from elsewhere are still held.
- AGENTS block (and this repo's AGENTS.md/CLAUDE.md via `agents-md --claude`) and the MCP
  instructions tell agents lists come in queue order and to take the first task. README updated.

Tests: 15 new (62 total, all green).

Verified in Chrome. Native HTML5 drag cannot be completed by synthetic mouse input (dragstart
fires, drop/dragend never do), so drops were driven with real DragEvents + DataTransfer at real
card coordinates: within-column reorder (rank 1500 between 1000 and 2000), cross-column drop at
a position, drop into an empty column, the drop line, own change rendering while hovering, and
an external CLI rank still held until the pointer left. Move to top tested with a real click.

## 2026-09-29T20:58:56Z — aiboard-lead

Status changed `in-progress` → `done`. manual ordering shipped; 62 tests green; verified in Chrome

