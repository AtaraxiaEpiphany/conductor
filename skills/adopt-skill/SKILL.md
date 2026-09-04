---
name: adopt-skill
description: Adopt an outside skill into the conductor — two roads. Road A adopts it as a project TASK TYPE (tag row + workflow docfile; [<Tag>] tasks run the skill's procedure inside task-executor). Road B adopts it as a WRAPPER AGENT (.claude/agents/<name>.md + agent-roster row). Each road is one validated command.
when_to_use: User wants a skill (from another plugin, the marketplace, or their own collection) to run under conductor dispatch — either woven into task execution as a tagged task type (Road A) or as a dedicated dispatched agent with full scaffold (Road B). Not for skills the user just wants to invoke manually.
argument-hint: "[road: tag|wrapper] <Tag>|<name> --skill <skill> [--signals \"a,b\"] [--gates tdd,coverage,checkpoint] [--grounding test|review|data-check|human-attest] | [--description <text>] [--class <c>] [--recovery <kind>] [--force]"
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
model: sonnet
---

# Adopt a skill into the conductor

You are a **thin front door** on two roads. The generators
(`track-state tag add`, `track-state roster add`) do everything — validation,
overlay row, wrapper file, cache clear. Your job: pick the road, parse the
arguments, run the generator(s), relay results verbatim. Do not hand-edit the
overlay JSON, the wrapper, or — beyond writing it once — the docfile.

## 0.0 PARSE

`$ARGUMENTS` must contain `--skill <skill>` for both roads.

- Road A shape: a `<Tag>` (positional after the road word `tag`, or the first
  bare token when the arguments clearly name a task type).
- Road B shape: a `<name>` plus optional `--description`, `--class`,
  `--fence`, `--recovery`, `--recovery-instruction`, `--force`,
  `--project-dir`.
- Missing the road or the required token → go to §0.5.

## 0.5 CHOOSE THE ROAD

If `$ARGUMENTS` do not already name a road (`tag` / `task-type` → Road A;
`wrapper` / `agent` → Road B), ask ONE `AskUserQuestion`:

- **Run as a task type (Road A)** — `[<Tag>]` tasks execute the skill's
  procedure inside task-executor, inheriting the conductor scaffold (safety
  floor, result fence, recovery) for free. Best when the skill is a *way of
  doing a task*.
- **Run as a wrapper agent (Road B)** — a dedicated subagent preloads the
  skill and is dispatched by name. Best when the skill is a *role* the
  conductor should treat as its own agent.

## ROAD A — task type + workflow docfile

### A1. PICK THE TAG

Run `track-state registry-doc` (the resolved tag table). Then ONE
`AskUserQuestion`: adopt onto an **existing tag** (its row gains the
docfile; you will pass `--force`) or create a **new tag** (the default — an
adopted procedure is usually bespoke; an existing tag that already fits the
skill's meaning is the exception).

### A2. WRITE THE ROW (FIRST — before any docfile work)

For a new tag, ONE `AskUserQuestion` for the gates, defaulting to **no
exemptions** (full TDD — dropping a gate is the user's explicit call, never
yours), plus a one-line `when_to_use`. Then:

```bash
track-state tag add <Tag> --when-to-use "<one line>" [--signals "a,b"] \
  [--gates tdd,coverage,checkpoint] [--grounding test|review|data-check|human-attest] \
  --workflow-doc <docfile>.md
```

- Add `--force` when adopting onto an existing tag.
- The docfile name is a bare sanitized `.md` filename (letters/digits/`-`/`_`/`.`,
  no path separators) — typically the skill's name: `my-skill.md`.
- Non-zero exit (`ok: false` with `errors`): print the errors verbatim, then
  STOP — same discipline as Road B's §3.0.

The row lands BEFORE the docfile exists on purpose: until the docfile is
written, the tag resolves fail-open to default TDD with a loud warning —
never a broken dispatch. A failed distillation (A3) leaves a safe tag, not a
dangling one.

### A3. DISTILL THE DOCFILE

Read the skill's body (its `SKILL.md`). Then Write ONE docfile at
`conductor/workflow/steps/<docfile>.md`, shaped like the shipped `Migrate`
docfile (see it via `track-state registry-doc --tag Migrate`):

- a title naming the tag (e.g. `# [MySkill] Workflow — …`);
- an intro that names the `workflow_doc` pointer, says it replaces the
  default TDD cycle for `[<Tag>]` tasks, and that a project may override it;
- a numbered, executor-facing procedure distilled from the skill — what
  task-executor should DO, step by step, self-contained (no skill invocation,
  no router prose, no frontmatter).

### A4. RELAY

Print the tag-add JSON output **verbatim**, then orientation: the row lives
at `conductor/workflow/task-type-profiles.json`, the docfile at
`conductor/workflow/steps/<docfile>.md`, and tasks opt in by being named
`[<Tag>] <description>` (the tag also joins the init lint's signal suggestions
only if the user later opts in with `--auto-propose` — the row writes it false
by default).

## ROAD B — wrapper agent (unchanged)

### 1.0 PARSE

`$ARGUMENTS` must contain a `<name>` and a `--skill <skill>`. Optional:
`--description`, `--class`, `--fence`, `--recovery`, `--recovery-instruction`,
`--force`, `--project-dir`.

- Missing either → print `usage: /conductor:adopt-skill <name> --skill <skill>`; STOP.
- `--class` / `--recovery` values are validated by the generator; relay its
  error verbatim if it rejects them.

### 2.0 RUN

```bash
track-state roster add <name> --skill <skill> [extra flags...]
```

Pass the user's flags through unchanged.

### 3.0 RELAY

Print the command's JSON output **verbatim**, then one line of orientation:
the wrapper lives at `.claude/agents/<name>.md` (edit its body freely — the
generator only refuses to clobber it without `--force`), the scaffold row at
`conductor/workflow/agent-roster.json`.

On a non-zero exit (the output carries `ok: false` with `errors`): print the
errors verbatim, then STOP. Do not attempt to fix the errors yourself — they
name the exact flag or file to change.

## Notes

- Road B defaults mirror the task-executor scaffold (executor class,
  `---TASK RESULT---` fence, result-file recovery) — most adoptions need only
  `<name> --skill`. Road A defaults are the safe ones: executor route, both
  gates on, `auto_propose: false`.
- Read-only apart from the generated files (Road A: the overlay row + one
  docfile; Road B: the wrapper + roster row); both keep a `.bak` of the JSON.
