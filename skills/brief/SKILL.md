---
name: brief
description: Interactively gather comprehensive track context and write a brief.md that /conductor:new-track consumes as authoritative planning input
when_to_use: User wants to capture full track context before planning, or has comprehensive track info and wants a durable brief; produces conductor/tracks/<id>/brief.md for /conductor:new-track to auto-detect
argument-hint: "[track_description]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion
model: sonnet
---

# Conductor Brief

A **Track Brief** is the comprehensive, durable capture of a track's full context — the *why*, goals, explicit out-of-scope, constraints, stakeholders, open questions, and draft acceptance signals — written **before** planning. It lives at `conductor/tracks/<track_id>/brief.md` and is consumed as authoritative input by `/conductor:new-track` (§2.2b Brief Detection), which skips its own Q&A when a Brief is present and feeds the Brief's sections to spec-planner.

**Flow:** `/conductor:brief` (this skill — think + gather + write) → human reviews/edits `brief.md` → `/conductor:new-track <track_id>` (plan, consuming the Brief). Do NOT auto-chain into new-track — the hand-off is manual on purpose.

## 0.0 RESOLVE PATHS

Key paths (resolve via `conductor/index.md` if non-default):
- Product: `conductor/product/product.md`
- Tech Stack: `conductor/design/tech-stack.md`
- Tracks Registry: `conductor/tracks.md`
- Workflow Index: `conductor/workflow/index.md`

## 0.5 RESUME CHECK

A brief run can be interrupted before `brief.md` is written. A lightweight progress marker — created and deleted by the `track-state brief-*` commands (the orchestrator **never** hand-edits the JSON) — lets an interrupted run be detected.

1. Detect any interrupted brief:
   ```bash
   track-state brief-resume
   ```
   Parse the JSON. Switch on `action`:
   - `none` → fresh brief → proceed to §1.0.
   - `resume` → `candidates[]` is the partial brief(s). For each candidate, `AskUserQuestion`:
     *"Found an interrupted brief for `<track_id>` (brief.md `present`/`absent`). Resume?"*
     - **Resume** → adopt the candidate's `track_id` / `track_dir`. If `brief_present`, Read the existing `brief.md` and re-interview only the gaps/sections the user wants to revise; otherwise start fresh into that track_dir. Jump to §2.0.
     - **Discard** → `track-state brief-finalize "<track_dir>"` (removes the stale marker), then proceed to a fresh brief (§1.0).

The marker is created in §1.1 (`brief-init`) and deleted at §5 hand-off (`brief-finalize`).

## 1.0 SETUP CHECK

1. Verify via project CLAUDE.md TOC: Tracks Registry, Product Definition, Tech Stack, Workflow Index.
2. If ANY missing → halt: `"Conductor environment incomplete — missing: <files>. Run /conductor:setup."`

CRITICAL: Validate every tool call. On failure → halt → announce.

## 1.1 TRACK ID

1. Get the track description from `$ARGUMENTS` (or `AskUserQuestion` if absent). This is the *seed* — §3 gathers the comprehensive context around it.
2. **If `$ARGUMENTS` is a bare track_id** matching an existing track dir under `conductor/tracks/` → adopt that track_id/dir directly (do not re-derive). This lets the user re-run `/conductor:brief <existing_id>` to revise an existing brief.
3. Otherwise **derive the track id deterministically** — pick a short slug (1–3 lowercase words) summarizing the track, then run:
   ```bash
   track-state derive-name <slug>
   ```
   Parse the JSON. Use `track_id` and `track_dir` from the result for everything below. Never hand-write the date.
4. **Initialize resume marker** (skip if resuming from §0.5 — the marker already exists). Idempotent:
   ```bash
   track-state brief-init "<track_dir>" --track-id <id>
   ```
5. Ensure `<track_dir>/` exists (`mkdir -p`) so the writer has a home. Do NOT create `track-state.json` — a Brief is pre-state.

## 2.0 CONTEXT DISCOVERY (Paths Only)

1. **Scan & Match:** Search `conductor/index.md` for file paths semantically related to the track's goal.
2. **Found relevant docs** → collect paths only (do NOT read contents). These become `CONTEXT_PATHS` for the writer (§4).
3. **Not found** → `CONTEXT_PATHS = N/A`. The §3 interview carries the context instead.

> Context content is loaded by the writer itself (§3). The orchestrator handles only paths and the interview answers.

## 3.0 INTERACTIVE INTERVIEW

Gather the comprehensive context. **Batch related questions into a single `AskUserQuestion` call where possible (3–5 prompts total).** Cover only what §2 left ambiguous — do not interrogate. The goal is the things a thin description never captures:

- **Problem & Motivation** — why this track exists (the pain/opportunity/trigger).
- **Goals (in-scope)** — concrete, verifiable outcomes.
- **Out of Scope** — explicit exclusions (deferred features, rejected tech, edge cases not supported). **This is the most valuable section** — spec-planner copies it verbatim; surface it deliberately.
- **Context & Constraints** — tech-stack touchpoints, hard limits (perf/compat/security/deadlines), decisions already made.
- **Stakeholders / Reviewers** — who cares, who signs off.
- **Open Questions** — honest unknowns to resolve during planning (not blockers).
- **Suggested Acceptance Signals** — coarse, user-facing pass/fail conditions (one per goal is a good baseline).

**Surface what §2 observed** (e.g. "Found `conductor/design/api-specs/auth.md` — is this the auth boundary this track builds on?") and let the user confirm or correct — informed questions beat generic ones.

If the description already contains comprehensive context and §2 found unambiguous docs, **skip asking and proceed on defaults — one confirmation prompt that you're about to write is enough** (mirror strategy-writer §4).

Consolidate the answers (and any carried-over description) into a single `USER_ANSWERS` block for §4.

## 4.0 DISPATCH TRACK-BRIEF-WRITER

Dispatch `conductor:track-brief-writer`, prompt:

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
CONTEXT_PATHS={paths or N/A}
USER_ANSWERS={consolidated answers or N/A}
```

Parse the `---BRIEF RESULT---` block. Confirm `STATUS: SUCCESS` (halt on FAILURE and announce `SUMMARY`). `brief.md` is now on disk.

## 4.1 VALIDATE

Re-read `<track_dir>/brief.md` exists and has the load-bearing structure spec-planner depends on:

1. Frontmatter contains `track_id:` and `provenance:`.
2. The `## Out of Scope` heading is present (the one section that must be explicit, not inferred).

If either is missing → re-dispatch `conductor:track-brief-writer` ONCE with the defect appended to a `PREVIOUS_ERRORS` line (`REGEN_FOCUS: the brief is missing <frontmatter|## Out of Scope>; re-read ${CLAUDE_PLUGIN_ROOT}/templates/brief-scaffold.md and regenerate a conforming brief.md`). Re-parse the result. Still failing → **halt**: `"Brief-writer produced a brief.md that still fails validation — inspect <track_dir>/brief.md."`

## 5.0 CONFIRM + HAND-OFF

1. Announce: *"Track brief written to `<track_dir>/brief.md`."*
2. `AskUserQuestion`: *"Brief ready. Open it to review/edit now?"*
   - **Yes, review/edit** → the user edits `brief.md` in place (they may refine Out-of-Scope, add open questions, etc.).
   - **No, looks good** → proceed.
3. **Finalize resume marker** (brief is durable now):
   ```bash
   track-state brief-finalize "<track_dir>"
   ```
   Parse the JSON. If `brief_present: false` → warn: *"Marker finalized but brief.md is missing — re-run /conductor:brief <track_id>."` (finalize reports the check; it does not hard-fail so cleanup always succeeds).
4. Print the hand-off:
   > **Brief ready at `<track_dir>/brief.md`.**
   > When ready to plan, run: `/conductor:new-track <track_id>`
   > It will auto-detect this Brief and use it as authoritative planning input (skipping its own Q&A).

Do NOT auto-chain into `/conductor:new-track`. The hand-off is manual — the user may want to edit the Brief, sit on it, or hand it to someone else first.
