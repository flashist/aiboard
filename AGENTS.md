# Working with aiboard as an agent

This board lives in the filesystem. You can use the CLI or edit files directly.
Prefer the CLI; it keeps sprint membership, worklogs and timestamps consistent.

Set your name once so worklog entries are attributed:

```bash
export AIBOARD_AUTHOR=my-agent-name
export AIBOARD_ROOT=/path/to/board   # if not the current directory
```

## Loop

1. **Find work.** `aiboard --json task list --status backlog --sprint S-001`
2. **Claim it.** `aiboard task move T-007 in-progress` and, if needed, `aiboard task edit T-007 --assignee my-agent-name`
3. **Read the brief.** `aiboard --json task show T-007` (the `brief` field is Markdown).
4. **Work, and log as you go.** `aiboard task log T-007 "Implemented X. Next: Y."` Log decisions, blockers, and anything a human or the next agent would need. Use `-` to pipe a longer Markdown message via stdin.
5. **Finish.** `aiboard task move T-007 done --note "PR #12 merged"` or `aiboard task move T-007 cancelled --note "superseded by T-009"`.
6. **Verify.** `aiboard check` exits 0 when tasks and sprints agree.

## Creating work

```bash
aiboard --json task new "Short imperative title" --sprint S-001 --priority high \
  --label backend --body-file - <<'EOF'
Context, acceptance criteria, links.
EOF
```

## Rules

- Never edit a task's status inside `brief.md`; status is the folder. Use `aiboard task move` or `mv`.
- `worklog.md` is append-only. Do not rewrite history.
- One task per folder, one folder per task. Ids (`T-001`) are permanent; the slug after the id is cosmetic.
- If you edit files by hand, run `aiboard check` before you finish.
