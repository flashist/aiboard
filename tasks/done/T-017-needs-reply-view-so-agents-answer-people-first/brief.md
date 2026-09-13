---
id: T-017
title: needs-reply view so agents answer people first
sprint: S-003
priority: high
assignee: 
labels:
  - cli
  - mcp
blocked_by: []
created: "2026-09-13T09:51:37Z"
updated: "2026-09-13T09:51:37Z"
---

# needs-reply view so agents answer people first

Second emulation: a human comment landed three seconds after the agent closed the task and went unnoticed. Added last_comment_by / needs_reply, task list --needs-reply, MCP filter, 'reply?' marker, and step 0 in the agent instructions. Wave 2 agents answered every pending comment before picking new work.
