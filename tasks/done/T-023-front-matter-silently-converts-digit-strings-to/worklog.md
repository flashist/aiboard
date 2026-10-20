# Worklog

## 2026-09-18T12:50:09Z — aiboard-lead

Task created in `backlog`.

## 2026-10-20T14:31:55Z — aiboard-lead

Assigned to `aiboard-lead`.

## 2026-10-20T14:31:55Z — aiboard-lead

Status changed `backlog` → `in-progress`.

## 2026-10-20T14:33:15Z — aiboard-lead

Fixed, at fkit-lead's request (owner-approved, gate for fkit Sprint 14). Uncommitted.

- `aiboard/model.py` `_parse_scalar`: only canonical ints become ints (`str(int(raw)) == raw`), so `0013`/`0404`/`007`/`-0` stay strings. Plain `5`, `-3` still read as ints (existing test `n: 3` and stored `rank` rely on that).
- `_dump_scalar`: quote any string that would not read back as itself (`_parse_scalar(s) != s`), so the string `"404"` stays a string, and `~`/`None`/`True` strings no longer turn into null/bool.
- Tests: `test_zero_padded_digits_roundtrip_byte_identical` (`0013`, `0404` come back byte-identical) and `test_hostile_scalars_roundtrip` (the brief's table). Both pass.
- fkit corpus (read-only, in memory): 538 files (535 briefs + 3 sprint files), 2870 single-line `## Field` values stored as front matter: 0 failures. Before the fix: 535/535 briefs failed (`id: 0013` -> `13`).
- Not fixed, out of scope: `None` and `""` inside a block list are dropped on read. Pre-existing unrelated failure: `test_comments_and_stale_detection` hard-codes `2026-09-` dates and fails on clean HEAD too.

## 2026-10-20T14:33:15Z — aiboard-lead

Status changed `in-progress` → `done`. Fixed in working tree, uncommitted; fkit corpus 538/538 round-trip

