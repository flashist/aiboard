---
id: T-026
title: Phase-1 handover cleanups for fkit's Node port
sprint: S-003
priority: high
rank: 26000
assignee: aiboard-lead
labels: []
blocked_by: []
created: "2026-10-20T14:49:35Z"
updated: "2026-10-20T14:49:48Z"
---

# Phase-1 handover cleanups for fkit's Node port

# Phase-1 handover cleanups for fkit's Node port

Requested by fkit-lead, owner-approved 2026-10-20. fkit ports aiboard to Node against the code at `a1592dc`.

- Commit the T-023 corpus check as a reproducible script (read-only on fkit).
- Make `test_comments_and_stale_detection` date-independent (test-only).
- Ignore `.DS_Store` and stop tracking the 3 committed copies (no history rewrite).

Freeze: after these, no changes to `aiboard/*.py` or the web page while fkit's Sprint 14 port runs.
Do not fix the block-list None/"" gap; fkit decides in a later phase.
