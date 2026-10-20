---
id: T-023
title: Front matter silently converts digit strings to ints, destroying zero-padding
sprint: S-003
priority: high
assignee: aiboard-lead
labels: []
blocked_by: []
created: "2026-09-18T12:50:09Z"
updated: "2026-10-20T14:33:15Z"
---

# Front matter silently converts digit strings to ints, destroying zero-padding

Data corruption, silent, on the first write. Found 2026-09-18 while assessing whether
aiboard can hold fkit's 404-task corpus as system of record.

## Demonstrated through the public API

```python
t = board.create_task("label probe", labels=["0404", "0013", "ok-label"])
board.get_task(t.id).meta["labels"]   # -> [404, 13, 'ok-label']
```

The file on disk then literally reads `  - 404`. No error, no warning, and a second
write does not restore it — the original string is gone.

## Cause

`model._parse_scalar` ends with `re.fullmatch(r"-?\d+", raw)` -> `int(raw)`, and
`_dump_scalar` writes an int back unquoted. So the round trip is lossy for any string
of digits:

| written  | read back | note |
|----------|-----------|------|
| `"0404"` | `404`     | zero-padding destroyed |
| `"0013"` | `13`      | zero-padding destroyed |
| `"007"`  | `7`       | zero-padding destroyed |
| `"404"`  | `404`     | type changed str -> int |
| `"-3"`   | `-3`      | type changed str -> int |

Non-digit values round-trip correctly, including emoji, colons, brackets, quotes,
backslashes, leading/trailing spaces and `true`/`false`/`null` (those are quoted).
So this is specifically the all-digits case.

## Why it is now urgent

fkit's task ids are **zero-padded four-digit strings** (`0404`, `0013`). The owner has
ruled that aiboard becomes the single store for fkit's corpus. Any fkit id that lands
in a front-matter field — an id field under a configurable id scheme, a `blocked_by`
entry, a label, a cross-reference — is silently renumbered on the first write.
`0013` becomes `13`, which is a *different task*.

## Fix

Keep the parsed value a string unless the document says otherwise. Options:
1. Drop int coercion from `_parse_scalar` entirely; callers that want a number convert.
   Nothing in aiboard currently relies on front-matter ints.
2. Coerce only when the digits have no leading zero and the value round-trips
   (`str(int(raw)) == raw`), which preserves `0404` while still typing `5`.

Option 1 is cleaner and is what a tracker wants: identifiers are strings.

## Acceptance criteria

- `"0404"` written to any front-matter field reads back as `"0404"`.
- A test round-trips a table of hostile values: zero-padded digits, plain digits,
  negative numbers, `true`/`false`/`null`, emoji, colons, brackets, quotes,
  backslashes, leading/trailing whitespace, empty string.
- Existing boards keep loading; no migration needed for values that are genuinely ints.

## What was verified alongside this, and passed

All 408 of fkit's real briefs were imported into a scratch board and read back:
**0 errors, 0 titles corrupted, 0 bodies lost.** The body and title paths are sound
against real content. This defect is confined to front-matter scalar fields.
