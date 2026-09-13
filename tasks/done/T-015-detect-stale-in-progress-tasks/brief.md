---
id: T-015
title: Detect stale in-progress tasks
sprint: S-003
priority: medium
assignee: claude
labels:
  - cli
blocked_by: []
created: "2026-09-13T07:48:54Z"
updated: "2026-09-13T09:51:36Z"
---

# Detect stale in-progress tasks

A task that lost a claim race was left in-progress with nobody working on it and `check` could not tell. Consider: `check` warns when an in-progress task has had no worklog entry for N hours (configurable in aiboard.json).
