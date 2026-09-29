# Worklog

## 2026-09-29T20:43:22Z — aiboard-lead

Task created in `backlog`.

## 2026-09-29T20:43:22Z — aiboard-lead

Assigned to `aiboard-lead`.

## 2026-09-29T20:43:22Z — aiboard-lead

Status changed `backlog` → `in-progress`.

## 2026-09-29T20:47:52Z — aiboard-lead

Implemented.

- `model.py`: `PRIORITIES` is now `low, medium, high, critical`. New `PRIORITY_ALIASES`
  (`urgent` -> `critical`), `canonical_priority()` and `normalize_priority()`. `Task.priority`
  resolves aliases, so a hand-edited `priority: urgent` still reads as critical.
- `store.py`: `create_task` and `update_task` normalise through one `_priority()` helper, so
  the file only ever holds the canonical name. Error message unchanged.
- `cli.py`: `--priority` takes `type=canonical_priority` before `choices`, so `urgent` works
  and bad values still get argparse's "invalid choice" message. List PRIO column widened to 8.
- `mcp.py`: shared `PRIORITY_PROP`; the description tells agents what critical means.
- `web/index.html`: critical in both dropdowns; critical cards get a 5px red border, red
  tint and an uppercase CRITICAL chip. Padding compensates so text stays aligned.
- README: command table, front-matter reference, and a paragraph on what critical is for.

Tests: 6 new (model, store, hand-edited alias, CLI, HTTP, MCP). `test_argparse_errors_are_json`
used `urgent` as its invalid example; switched to `bogus`, intent unchanged. 47/47 pass.
The 104 ResourceWarnings from test_board are pre-existing (identical count at HEAD).

Verified in Chrome on a scratch board: critical cards stand out, text edges align (45px on
every card), the drawer dropdown sets critical through the API and the card restyles.

Noticed, not changed: editing priority writes no worklog entry (true of all field edits).
Priority still does not affect ordering anywhere.

## 2026-09-29T20:47:52Z — aiboard-lead

Status changed `in-progress` → `done`. critical priority shipped; 47 tests green

