---
id: T-018
title: Hold board re-renders while the pointer is over the board
sprint: S-003
priority: medium
assignee: 
labels:
  - web
blocked_by: []
created: "2026-09-13T09:51:37Z"
updated: "2026-09-13T09:51:37Z"
---

# Hold board re-renders while the pointer is over the board

Acting as a human in Chrome, three clicks in a row hit the wrong card because auto-refresh moved cards under the cursor while agents worked. The page now holds an incoming update while hovering or dragging and applies it when the pointer leaves.
