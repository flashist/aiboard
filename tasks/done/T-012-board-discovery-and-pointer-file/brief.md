---
id: T-012
title: Board discovery and pointer file
sprint: S-002
priority: medium
assignee: 
labels:
  - cli
created: "2026-09-13T06:27:57Z"
updated: "2026-09-13T06:27:57Z"
---

# Board discovery and pointer file

The CLI walks up from cwd like git. `aiboard init board` writes a pointer `aiboard.json` in the project root so a board kept in a subfolder is found from anywhere.
