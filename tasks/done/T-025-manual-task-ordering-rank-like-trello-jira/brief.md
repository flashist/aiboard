---
id: T-025
title: Manual task ordering (rank), like Trello/Jira
sprint: S-003
priority: high
assignee: aiboard-lead
labels: []
blocked_by: []
created: "2026-09-29T20:51:42Z"
updated: "2026-09-29T20:58:56Z"
---

# Manual task ordering (rank), like Trello/Jira

Requested by the owner 2026-09-29, following T-024.

Tasks must be reorderable like in Trello/Jira: drag a card to put it at the top of
the queue, others to the bottom. Today everything sorts by task id, a critical task
can sit below older work, and agents are not told which task to take first.

Design:
- One global `rank` integer per task, in its own front matter (Jira's model). Every
  view sorts by it: board columns, `task list`, MCP `list_tasks`, sprint views.
- Gaps of 1000; a move takes the midpoint of its neighbours, so one file changes.
  When a gap is exhausted, respace the order once.
- Tasks without a rank behave as `id x 1000`, so existing boards keep their order
  and need no migration.
- New tasks go to the bottom; `critical` tasks go to the top on creation and when
  raised to critical. Manual order wins after that.
- Rank writes do not touch `updated`, so reordering never makes a stale task look active.

Surfaces:
- CLI `aiboard task rank T-7 --top|--bottom|--before T-2|--after T-9`
- MCP `rank_task`; HTTP `POST /api/tasks/<id>/rank`
- Web: drag within and across columns with a drop indicator; Top/Bottom buttons in the drawer.
- Agent instructions: lists come in rank order, take the first task you can.

Also fix: the board holds re-renders while the pointer is over it (T-018), which
also holds the result of the user's own drop. User-initiated changes must show at once.
