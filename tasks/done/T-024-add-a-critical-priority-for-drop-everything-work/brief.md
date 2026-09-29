---
id: T-024
title: Add a critical priority for drop-everything work
sprint: S-003
priority: high
assignee: aiboard-lead
labels: []
blocked_by: []
created: "2026-09-29T20:43:22Z"
updated: "2026-09-29T20:47:52Z"
---

# Add a critical priority for drop-everything work

Requested by the owner 2026-09-29.

Add a fourth priority level above `high`, for work that arrives suddenly and needs
attention immediately — e.g. something is broken in production and must be fixed now.

- Canonical name `critical`; accept `urgent` as an alias (normalised to `critical` on write).
- Valid everywhere priority is: CLI (`task new`/`task edit`), MCP tools, HTTP API, web board dropdowns.
- Visually prominent on the web board, beyond the thin coloured border the other levels get.
- Documented in the README.

Out of scope (offer separately): sorting columns/lists by priority, and telling agents
in the AGENTS.md block to take critical tasks first.
