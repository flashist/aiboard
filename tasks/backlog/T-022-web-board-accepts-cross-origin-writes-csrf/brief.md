---
id: T-022
title: Web board accepts cross-origin writes (CSRF)
sprint: S-003
priority: high
assignee: 
labels: []
blocked_by: []
created: "2026-09-18T10:50:09Z"
updated: "2026-09-18T10:50:09Z"
---

# Web board accepts cross-origin writes (CSRF)

`aiboard serve` runs read-write by default and validates nothing about where a
request came from. Any page the user visits while the server is running can write
to the board.

## Demonstrated 2026-09-18

A POST from `Origin: https://evil.example` with `Content-Type: text/plain;charset=UTF-8`
(a CORS *simple* request, so the browser sends no preflight) closed a task:

```
before: backlog
HTTP 200 accepted the cross-origin simple request
after:  done
worklog says: Mark -> Status changed `backlog` → `done`.
```

The attacker also chose the author string, so the worklog records the close as the
owner's. The response body is unreadable cross-origin, so this is a blind write —
but every mutating endpoint is reachable: status changes, create, assign, edit,
comment, log, sprint moves.

## Cause

`server.py` never inspects `Origin`, `Referer` or `Content-Type`. `_body()` reads
`Content-Length` and `json.loads` the bytes, so a `text/plain` body that happens to
be JSON is accepted — which is exactly the shape that escapes CORS preflight.
There is no token, no session, no authentication of any kind (0 matches for
auth/token/session/cookie/csrf in `server.py`).

Binding to 127.0.0.1 does not help: the browser making the request is already inside.

## Fix options (pick one, probably the first two together)

1. Reject requests whose `Origin` header is present and is not the server's own origin.
2. Require `Content-Type: application/json` on mutating requests — this alone restores
   the CORS preflight, which the server does not answer, so cross-origin writes fail.
3. Longer term: a per-session token minted at page load, required on mutating requests.

## Acceptance criteria

- A cross-origin simple request to every mutating endpoint is rejected.
- Same-origin requests from the board page still work, including drag-to-status.
- A test reproduces the attack above and asserts it now fails.
- `--read-only` remains unaffected.

## Note for the terminal-UI discussion

This is an argument *for* a TUI that has nothing to do with identity: a terminal UI
removes the anonymous network write path entirely. It does not close the separate
identity gap — a TUI still cannot tell a human from an agent, since both run as the
same OS user.
