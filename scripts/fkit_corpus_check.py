"""Round-trip fkit's task briefs and sprint files through aiboard's front-matter reader/writer.

This is the corpus check T-023 was verified with ("the corpus as phase 1 ran it").
It is read-only on fkit: every file is read into memory and nothing is written anywhere.

Usage:  python3 -I scripts/fkit_corpus_check.py [FKIT_AI_AGENTS_DIR] [AIBOARD_CHECKOUT]
        defaults: ../fkit/ai-agents next to this repo, and this repo.
Exit code 0 when every file passes, 1 otherwise.

Files: FKIT/tasks/**/brief.md (any depth, any status folder) and FKIT/sprints/*.md, sorted.

fkit files have no front matter, so two checks run per file:
1. Read as-is: parse_front_matter(text) must return ({}, text), body byte-identical.
2. Field round-trip: fkit's values are stored as front matter on top of the original text.
   A field is a `## Heading` section whose first paragraph is a single line: the heading,
   then optional blank lines, then one line not starting with `#`, then a blank line or EOF.
   Its key is the heading lowercased with non-alphanumeric runs turned into `_` (`## ID` -> `id`,
   `## Blocked by` -> `blocked_by`); its value is that line as a string, verbatim.
   Multi-line values are not taken: a section whose first paragraph spans several lines is
   skipped entirely. If a section has a one-line first paragraph followed by more paragraphs,
   only that first line is taken. When a heading repeats, the first occurrence wins.
   Then: dump_front_matter(fields, text) -> parse_front_matter must give back equal fields
   (value and type); dumping the parsed result again must be byte-identical to the first dump;
   and the parsed body must equal the original text (ignoring trailing newlines, which
   dump_front_matter normalizes to one).
"""
import glob
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
fkit = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "..", "fkit", "ai-agents"))
sys.path.insert(0, os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..")))
from aiboard.model import dump_front_matter, parse_front_matter  # noqa: E402

FIELD = re.compile(r"^## (.+)\n+([^\n#][^\n]*)\n(?:\n|$)", re.M)


def fields(text):
    out = {}
    for m in FIELD.finditer(text):
        key = re.sub(r"[^a-z0-9]+", "_", m.group(1).strip().lower()).strip("_")
        out.setdefault(key, m.group(2))
    return out


def main():
    files = sorted(glob.glob(f"{fkit}/tasks/**/brief.md", recursive=True)) + sorted(glob.glob(f"{fkit}/sprints/*.md"))
    if not files:
        print(f"no files found under {fkit}")
        return 1
    raw_fail, field_fail, byte_fail, nfields = [], [], [], 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        if parse_front_matter(text) != ({}, text):
            raw_fail.append(f)
        meta = fields(text)
        nfields += len(meta)
        first = dump_front_matter(meta, text)
        meta2, body2 = parse_front_matter(first)
        if meta2 != meta or any(type(meta2[k]) is not type(v) for k, v in meta.items() if k in meta2):
            field_fail.append((f, {k: (v, meta2.get(k)) for k, v in meta.items() if meta2.get(k) != v}))
        if dump_front_matter(meta2, body2) != first or body2.rstrip("\n") != text.rstrip("\n"):
            byte_fail.append(f)

    rel = lambda f: os.path.relpath(f, fkit)  # noqa: E731
    print(f"fkit: {fkit}")
    print(f"files={len(files)} fields={nfields}")
    print(f"raw read failures={len(raw_fail)}")
    for f in raw_fail:
        print("  ", rel(f))
    print(f"field round-trip failures={len(field_fail)}")
    for f, bad in field_fail:
        print("  ", rel(f), bad)
    print(f"byte (write/read/write) failures={len(byte_fail)}")
    for f in byte_fail:
        print("  ", rel(f))
    return 1 if raw_fail or field_fail or byte_fail else 0


if __name__ == "__main__":
    sys.exit(main())
