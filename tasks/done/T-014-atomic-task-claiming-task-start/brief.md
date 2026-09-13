---
id: T-014
title: Atomic task claiming (task start)
sprint: S-003
priority: high
assignee: 
labels:
  - cli
  - mcp
blocked_by: []
created: "2026-09-13T07:48:54Z"
updated: "2026-09-13T07:48:54Z"
---

# Atomic task claiming (task start)

Play test with three concurrent agents produced four claim collisions in two minutes and one orphaned in-progress task. Added `task start` (Jira: Start progress) as an atomic claim under the write lock, made `assign` refuse to take a task from another holder without --force, and accepted --json anywhere on the command line.
