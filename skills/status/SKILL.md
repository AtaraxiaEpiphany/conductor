---
name: status
description: Project status overview — renders the code-owned track-state status backend (authoritative statuses, summary, issues, deferred). Read-only.
when_to_use: User wants track progress / a project overview. The runtime task-progress report. Distinct from /conductor:dashboard (the resolved-workflow DAG + gates view) — status is "where are the tasks", dashboard is "what is the workflow".
argument-hint: "[track_name] [--health]"
allowed-tools: Bash, Read
model: sonnet
---

# Conductor Status — Thin Renderer

You are a **thin renderer**. `track-state status` computes everything in code — track/phase **statuses are the authoritative stored values** (never re-derived), and the summary counts, issues, deferred list, and current position are all computed by the backend. Your ONLY job is to render its JSON envelope into the report below.

**Discipline (this is the whole point of the refactor):**
- Every value in your report comes **verbatim from the envelope**. You do NOT read `track-state.json`, and you do NOT compute, aggregate, count, or derive anything — that prose computation was the drift this refactor retires.
- If a field is `null` / absent / a section is empty, **omit** that line or section. Never invent values, SHAs, counts, or statuses.
- Render **every** track in `tracks`; do not summarize multiple tracks into one.

## 1.0 FETCH (once)

```bash
track-state status "$ARGUMENTS"
```

Empty `$ARGUMENTS` → all tracks; a track id / shortname / dir → that one track. Switch on the JSON:
- `ok: false, reason: "no_registry"` → print `hint`; STOP.
- `ok: false, reason: "no_match"` (or `ambiguous`) → print `hint` (and list `candidates` track_ids if present); STOP.
- `ok: true` → render §2.0 from `tracks` (a list) + `summary`.

## 2.0 RENDER — from the envelope only

### Status → marker map (apply to every task/subtask `status`)

`pending`→`[ ]` · `in_progress`→`[~]` · `completed`→`[x]` · `failed`→`[!]` · `skipped`→`[>]` · `deferred`→`[d]` · `blocked`→`[#]` · `cancelled`→`[-]`

### Summary

```
# Project Status Report

## Summary
- Total Tracks: <summary.total_tracks>
- <one "Status: N" clause per non-zero key in summary.by_status, e.g. "Completed: 3 | In Progress: 1 | Blocked: 1">
- Overall Progress: <summary.overall_progress.completed>/<summary.overall_progress.total> (<summary.overall_progress.pct>%)
- Deferred: <summary.deferred_count> tasks
```

### Per-track block — render ONE block per track in `tracks`

Split tracks into **Active** (status != `archived`) then **Archived** (status == `archived`); render archived blocks under a single `## Archived Tracks` heading (track_id + status only).

For each **active** track:

```
## Track: <track_id> — <description>
Status: <status> · Type: <type> · Shape: <shape>
Current: Phase <position.phase> · Task <position.task>[.<position.subtask>]: <position.name>
Progress: <progress.completed>/<progress.total> tasks

Phase <phases[].index>: <phases[].name> [<phases[].status>]
  <marker> <phases[].tasks[].index>: <name>[  <commit_sha first 7>][  retry <retry_count>/<max_retries>]
    <marker> <subtasks[].index>: <name>[  <commit_sha first 7>]
```

- For `state != "loadable"` tracks (`uninit` / `missing` / `ghost`), render the header line only with a note: `Status: <status> (state: <state> — no loadable state)`. Omit Current/Progress/phases.
- Omit `Current:` when `position.phase` is null. Omit `retry n/m` when `retry_count` is 0 or `max_retries` is null. Omit the SHA token when `commit_sha` is null.

### Issues (only if any track has a non-empty `issues` list)

```
## Issues Requiring Attention
### Track: <track_id>
- **<Failed|Blocked>**: Task '<issues[].name>' (Phase <issues[].phase>) — attempt <retry_count>/<max_retries>
  - Last failure: <last_failure_summary>      # only if kind == failed and field present
  - Skip analysis: <skip_analysis>            # only if kind == blocked and field present
```

### Deferred (only if any track has a non-empty `deferred` list)

```
## Deferred Tasks (awaiting manual verification)
### Track: <track_id>
- [ ] <deferred[].name> (Phase <deferred[].phase>) — <reason>
```

### Next Actions (recommend from `summary.by_status` only)

One line, picked by first match: any `in_progress`/`failed`/`blocked` → "Run `/conductor:implement` to continue; resolve failed/blocked tasks first." · all `completed` (no active) → "All tracks complete. Run `/conductor:review` or `/conductor:new-track`." · else → "Run `/conductor:implement` to start."

## 3.0 HEALTH CHECK (only if `$ARGUMENTS` contains `--health` or `--gc`)

After the report, for each **loadable** track run `track-state gc "<track_dir>"` (the `track_dir` field) and append:

```
## Health Check
- <track_id>: cleaned <gc.cleaned or 0> orphaned artifacts
```
