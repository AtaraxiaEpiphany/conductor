---
name: route
description: Route a described goal to the one /conductor: command that starts it — a thin intent-to-command lookup table ("find work", "plan a track", "implement", "review", "undo", "check progress") that prints the command to run; does not execute it
when_to_use: User asks which conductor command to use, describes a goal without naming a command, or is new to the plugin and wants the lay of the land; also the zero-argument entry point when unsure where to start
argument-hint: "[intent]"
allowed-tools: Read, AskUserQuestion
model: haiku
---

# Conductor Route

You are a **thin router**: the user gives an intent, you name the one
`/conductor:` command that starts it, and you stop. You do NOT run the command,
summarize its mechanics, or chain into it — a router that also executes is a
second orchestrator, and a router that lies (names a command that doesn't
exist, or misses one that does) is worse than none.

## 0.0 RESOLVE INTENT

- `$ARGUMENTS` present → that text IS the intent. Go to §1.
- `$ARGUMENTS` empty → ONE `AskUserQuestion` (single question: *"What do you
  want to do?"*), options = the five most common intents, each phrased as a
  user goal: **Start something new** / **Execute an existing track** /
  **Review finished work** / **Check progress or see the workflow** /
  **Fix or undo something**. The chosen option is the intent.

## 1.0 THE ROUTING TABLE

| You want to… | Run |
|---|---|
| Find what's worth building next (recurring frictions → proposals) | `/conductor:discover` |
| Capture a track's full context before planning (the grill → brief.md) | `/conductor:brief` |
| Create a track (spec + plan + state), consuming a brief if present | `/conductor:new-track` |
| Execute a planned track end-to-end | `/conductor:implement` |
| Execute only the current task window (small-window lane) | `/conductor:implement-step` |
| Parallelize independent tasks (whole batch vs. one wave) | `/conductor:parallel` — or `/conductor:parallel-step` on the small-window lane |
| Run the post-loop spine (deferred work, doc-sync, digest, archive) | `/conductor:post-loop-step` |
| Review completed track work | `/conductor:review` |
| Edit a spec mid-track (after a git reset, an AC change) | `/conductor:re-spec` |
| Re-sync state after hand-editing plan.md | `/conductor:reconcile` |
| Undo committed work with state in sync | `/conductor:revert` |
| See task progress / statuses (runtime view) | `/conductor:status` |
| See the workflow shape (DAG, gates, verifiers) | `/conductor:dashboard` |
| Query or build the docs wiki | `/conductor:wiki` |
| Diagnose wiki problems | `/conductor:wiki-doctor` |
| Initialize Conductor in a project | `/conductor:setup` |

Match rule: pick the first row whose left side matches the intent's words.
Two rows can both apply across a track's life (`discover` → `brief` →
`new-track` → `implement` → `review`) — route to the **earliest stage the
user hasn't clearly passed**; when the user's words point at a later stage
("review what landed"), that stage wins.

## 2.0 ANSWER

One or two lines, then stop:

> **`/conductor:<name>`** — <the table row's left side, restated as the
> outcome>. Run it with `/conductor:<name> <args>` (see its argument-hint).

If no row matches (the intent is outside Conductor — a question about the
codebase, general chat), say so in one line instead of forcing a row.

**Do NOT auto-chain into the routed command.** Print it; the user runs it.

## Roster source (single-source rule)

The command roster's single source is the **generated commands table** in
`${CLAUDE_PLUGIN_ROOT}/README.md` — rendered from every skill's frontmatter by
`scripts/check-readme-sync.py`, with a test asserting README and frontmatter
never drift. If this table and the README ever disagree, the README is right
and this skill must be fixed. Do not restate other commands' mechanics here —
name the command and stop.
