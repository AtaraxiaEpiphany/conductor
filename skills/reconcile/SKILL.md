---
name: reconcile
description: Re-sync track-state.json after a hand-edit of plan.md (git reset + tag/split/reorder), preserving commit SHAs
when_to_use: User did a git reset to undo a divergent task-executor run, then hand-edited plan.md (changed a task tag, split a task, reordered/reconstructed the remaining tasks) and wants state brought back in sync WITHOUT losing commit_sha on tasks whose work survived
argument-hint: "[track]"
allowed-tools: Bash, Read
model: sonnet
---

# Conductor Reconcile Plan

## 1.0 SYSTEM DIRECTIVE

You are a thin teleoperator for `track-state reconcile-plan`. The user has hand-edited `plan.md` (typically after a `git reset` that undid a divergent task-executor run) and needs `track-state.json` brought back in sync **by name, not position**, preserving `commit_sha` on tasks whose work is still correct.

All intelligence lives in the CLI. Your job: run the dry-run, read the diff back to the user, help them decide any `--rename` / `--drop` / `--clear-dangling` resolutions, then `--apply`. **Never edit `plan.md`, `track-state.json`, or any state file yourself** — the command owns the write.

**Core Protocols:** State Lock (F1). Resolve the track dir via the project CLAUDE.md TOC or `track-state resolve-track "<track>"`.

## 2.0 WHEN TO USE THIS (vs. the other sync paths)

| Situation | Command |
|---|---|
| You edited `plan.md` by hand (tag/split/reorder) after a reset, want SHAs kept | **`reconcile-plan`** (this skill) |
| State drifted from plan.md markers, want a plain re-render (state→plan) | `sync-plan` |
| Fresh start, wipe all progress | `init-from-plan --force` (destroys SHAs — rare) |
| Git-revert completed commits + reset state | `/conductor:revert` |

The load-bearing difference: `sync-plan` matches **positionally** — reorder a task above a completed one and its SHA silently rebinds to the wrong task. `reconcile-plan` matches **by phase + task name**, so a SHA stays on its named task across reorders.

## 3.0 PROCEDURE

### 3.1 Resolve the track
```
track-state resolve-track "<track>"     # confirm the track dir
```

### 3.2 Dry-run (default — writes nothing)
```
track-state reconcile-plan "<track>"
```
Read the JSON back. It classifies every node into buckets:

- **`unchanged`** — name + status + tags match state. No-op.
- **`tag_or_status`** — name matches but the marker and/or tag text differs (e.g. you flipped `[x]`→`[>]` or added `[Docs]`). **SHA is preserved when the new status is terminal.** This is the "change the tag, run sync" case.
- **`split`** — you added subtask lines under an existing task; they append as `pending` (parent SHA untouched).
- **`unmatched`** — a node exists on only one side. **The command refuses to apply until you resolve it** with `--rename` (you renamed a task) or `--drop` (you deliberately deleted it). No guess.
- **`dangling_sha`** — a terminal node's `commit_sha` is not reachable in git history (you `git reset` *past* its Complete commit). Reported as a warning; the terminal marker is respected. Use `--clear-dangling` to requeue the task as pending if the work should be redone.

### 3.3 Resolve conflicts (only if `unmatched` is non-empty)
Use the `hints` array verbatim — each entry is the exact flag to add:

- Renamed a task: `--rename "<phase>:<old name>=<new name>"`
- Deleted a task (its SHA is now unwanted): `--drop "<phase>:<task name or index>[.<subtask>]"`
- Dangling SHA you want requeued: `--clear-dangling "<phase>:<task>[.<subtask>]"`

`<phase>` is the phase number; `<task>` is the 1-based index or the task name. Flags are repeatable.

### 3.4 Apply
```
track-state reconcile-plan "<track>" --apply [--rename ... --drop ... --clear-dangling ...]
```
One transaction mutates state, `_do_sync_plan` re-renders `plan.md` once, and a single `chore(conductor): Reconcile plan edits` commit is made. Check `ok: true` and any `warnings`.

### 3.5 Verify the spine still works
```
track-state validate "<track>"      # clean
track-state step "<track>"          # dispatches the next pending piece correctly
```

## 4.0 GUARDRAILS

- **Dry-run first, always.** Never `--apply` without reading the diff. The whole point is the user doesn't trust the recent state.
- **Never hand-edit `track-state.json`.** If reconcile's diff looks wrong, the plan.md edit is ambiguous — fix the plan, re-run dry-run.
- **Unmatched = stop and ask.** Don't invent a `--rename`/`--drop`. Show the user the hint and let them confirm which task is which.
- **Reorder safety is the headline guarantee.** If a completed task's SHA lands on the wrong task after apply, that's a bug — halt and report it; do not paper over it.
