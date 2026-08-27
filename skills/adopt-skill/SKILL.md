---
name: adopt-skill
description: Adopt an outside skill as a conductor agent — generates the wrapper subagent (.claude/agents/<name>.md with skills-frontmatter preload) and its agent-roster overlay row in one validated command.
when_to_use: User wants a skill (from another plugin, the marketplace, or their own collection) to run as a conductor-dispatched executor — full scaffold (safety floor, result fence, recovery) with zero plugin edits. Not for skills the user just wants to invoke manually.
argument-hint: "<name> --skill <skill> [--description <text>] [--class <c>] [--recovery <kind>] [--force]"
allowed-tools: Bash, Read
model: haiku
---

# Adopt a skill as a conductor agent

You are a **thin front door**. The generator
(`track-state roster add`) does everything — validation, wrapper file,
overlay row, cache clear. Your job: parse the arguments, run it, relay the
result verbatim. Do not hand-edit the wrapper or the roster JSON.

## 1.0 PARSE

`$ARGUMENTS` must contain a `<name>` and a `--skill <skill>`. Optional:
`--description`, `--class`, `--fence`, `--recovery`, `--recovery-instruction`,
`--force`, `--project-dir`.

- Missing either → print `usage: /conductor:adopt-skill <name> --skill <skill>`; STOP.
- `--class` / `--recovery` values are validated by the generator; relay its
  error verbatim if it rejects them.

## 2.0 RUN

```bash
track-state roster add <name> --skill <skill> [extra flags...]
```

Pass the user's flags through unchanged.

## 3.0 RELAY

Print the command's JSON output **verbatim**, then one line of orientation:
the wrapper lives at `.claude/agents/<name>.md` (edit its body freely — the
generator only refuses to clobber it without `--force`), the scaffold row at
`conductor/workflow/agent-roster.json`.

On a non-zero exit (the output carries `ok: false` with `errors`): print the
errors verbatim, then STOP. Do not attempt to fix the errors yourself — they
name the exact flag or file to change.

## Notes

- Defaults mirror the task-executor scaffold (executor class, `---TASK RESULT---`
  fence, result-file recovery) — most adoptions need only `<name> --skill`.
- Read-only apart from the two generated files; keeps a `.bak` of the roster.
