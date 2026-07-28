---
name: brief
description: Grill the user one question at a time to reach shared understanding of a track, then write a brief.md that /conductor:new-track consumes as authoritative planning input
when_to_use: User wants to capture full track context before planning, or has comprehensive track info and wants a durable brief; produces conductor/tracks/<id>/brief.md for /conductor:new-track to auto-detect
argument-hint: "[track_description]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion
model: sonnet
---

# Conductor Brief

A **Track Brief** is the comprehensive, durable capture of a track's full context — the *why*, goals, explicit out-of-scope, constraints, stakeholders, open questions, and draft acceptance signals — written **before** planning. It lives at `conductor/tracks/<track_id>/brief.md` and is consumed as authoritative input by `/conductor:new-track` (§2.2b Brief Detection), which skips its own Q&A when a Brief is present and feeds the Brief's sections to spec-planner.

**Flow:** `/conductor:brief` (this skill — grill to shared understanding, then write) → human reviews/edits `brief.md` → `/conductor:new-track <track_id>` (plan, consuming the Brief). Do NOT auto-chain into new-track — the hand-off is manual on purpose.

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

1. Get the track description from `$ARGUMENTS` (or `AskUserQuestion` if absent). This is the *seed* — §3 grills the comprehensive context out of it.
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
2. **Found relevant docs** → collect paths only (do NOT read contents). These become `CONTEXT_PATHS` for the writer (§4), AND fuel the §3 recommendations.
3. **Not found** → `CONTEXT_PATHS = N/A`. The §3 interview carries the context instead.

> The orchestrator may Read the discovered docs itself during §3 to ground its recommended answers (look-it-up-first, see §3). The writer still loads full content itself.

## 3.0 GRILL TO SHARED UNDERSTANDING

> **MUST — one question at a time, via `AskUserQuestion`, no exceptions.**
> Every decision below is posed as a **single** `AskUserQuestion` call, and you
> **wait for the answer before posing the next one.** Never batch two decisions
> into one prompt; never free-text a question as plain prose instead of calling
> the tool. A `Write` to `brief.md` is denied by the `on-brief-grill-tripwire`
> hook until every tree node is resolved (the marker is `committed:false`), so
> skipping the grill cannot reach the write — but the grill's *quality* still
> depends on you asking one at a time. Asking multiple questions at once is
> bewildering; the user can't give each decision the thought it deserves.

Goal: reach a **shared understanding** of the track before anything is written.
Interview the user **relentlessly but one question at a time** — never batch.
Walk the decision tree, resolving dependencies one-by-one so each answer
informs the next question.

**The grill loop (one decision per iteration):**

1. **Pick the next decision** from the dependency-ordered tree below — the first
   not yet resolved. Don't jump ahead; later questions depend on earlier ones.
2. **Look it up before you ask.** If a fact can be found by exploring the
   environment — reading the §2 discovered docs, `conductor/product/product.md`,
   `conductor/design/tech-stack.md`, the codebase, or `conductor/purpose.md` —
   look it up rather than asking. The *decisions* are the user's; the *facts*
   are yours to gather. Never ask a question you could answer by reading.
3. **Pose ONE question** via `AskUserQuestion`, and **provide your recommended
   answer as the first option** (marked "(Recommended)") with a one-line
   rationale grounded in what you read. The user confirms, corrects, or picks
   "Other." A grilling without recommendations is just an interrogation — you
   are an expert collaborator, not a stenographer.
4. **Record the answer**, note any new dependency it opens (e.g. an Out-of-Scope
   decision may raise a fresh Open Question), and loop to step 1.

**Ask the questions one at a time, waiting for feedback on each before
continuing. This is non-negotiable.**

### The decision tree (resolve top-to-bottom — parents before children)

The `## ` sections in `brief.md` form a tree, not a flat checklist:

1. **Problem & Motivation** *(root)* — why this track exists: the pain,
   opportunity, or trigger. Read `product.md` / `purpose.md` first to ground the
   recommendation. Everything downstream is justified by this.
2. **Goals (in-scope)** *(depends on #1)* — concrete, verifiable outcomes.
   Recommend goals derived from the motivation + discovered docs. Each goal
   should trace back to a problem it solves.
3. **Out of Scope** *(depends on #2 — MOST VALUABLE SECTION)* — explicit
   exclusions: deferred features, rejected tech, edge cases not supported. This
   is the inverse of Goals — recommend scope cuts the discovered context
   implies (e.g. *"purpose.md already excludes X — recommend this track inherit
   that exclusion"*) and let the user confirm. spec-planner copies this section
   **verbatim**, so surface it deliberately and never infer it silently.
   Intersect with, never contradict, `purpose.md` Out-of-Scope.
4. **Context & Constraints** *(depends on #2, #3)* — tech-stack touchpoints and
   hard limits (perf/compat/security/deadlines). Read `tech-stack.md` and the
   discovered docs; recommend the constraints they imply. Decisions already made
   belong here too.
5. **Stakeholders / Reviewers** — who cares, who signs off. Look up team/owner
   from docs where possible; only ask what you can't find.
6. **References** *(depends on #2, #3)* — files, designs, or external links the
   user wants this brief to cite. §2 already discovered *docs* as
   `CONTEXT_PATHS`; this node is for **anything extra the user names** — a source
   file (`src/auth/session.py`), an external design (Figma), a ticket, a vendor
   doc. Ask *"Any files, designs, or links this brief should reference beyond
   what I found?"* Capture raw paths verbatim into `USER_REFERENCES` (paths
   only — the writer doesn't need contents); the §4 write unions them into
   `## References`. "Just the ones you found" is a valid answer.
7. **Open Questions** *(depends on all above)* — honest unknowns to resolve
   *during planning* (not blockers). Many emerge from earlier answers — an
   ambiguous Goal or an unconfirmed constraint becomes an Open Question rather
   than a guessed answer. "None identified." is a valid, honest result.
8. **Suggested Acceptance Signals** *(depends on #2)* — coarse, user-facing
   pass/fail conditions, roughly one per Goal. Recommend a draft signal per goal.

### Cadence & escape hatches

- **Informed, not generic.** Surface what §2 observed (e.g. *"Found
  `conductor/design/api-specs/auth.md` — recommend treating the auth boundary
  there as out-of-scope for this track. Confirm?"*) and let the user confirm or
  correct. Informed questions with a recommended answer beat generic ones.
- **Skip a branch only when it's genuinely resolved.** If the `$ARGUMENTS`
  description plus the §2 docs already fully answer a decision, **state your
  understanding and ask the user to confirm that one point** ("I'm reading this
  as: the goal is X, out-of-scope is Y — correct?") rather than re-asking from
  scratch. Do *not* silently default — a confirmation prompt is the floor, not
  a skip.
- **Do not write until shared understanding is reached.** The grill loop ends
  only when every tree node is resolved-or-confirmed. Then — and only then —
  proceed to §4. A brief written from guesses is worse than no brief.

Consolidate the answers (and any carried-over description) into a single
`USER_ANSWERS` block for §4, structured by the sections above, plus any
`USER_REFERENCES` captured in node 6.

**Signal grill-done before writing.** The moment shared understanding is reached,
run:

```
track-state brief-grill-done "<track_dir>"
```

This sets `grill_complete: true` on the brief marker, which is the **real gate**
the `on-brief-grill-tripwire` hook checks — NOT the raw `AskUserQuestion` count.
The count is only a backstop, and it's a *proxy* that's wrong exactly when you
do this well: many decisions are pre-resolved by reading docs / `$ARGUMENTS`
(§3 step 2), so a grill done in **fewer than six** questions is legitimate.
Signaling explicitly is how you tell the hook *"the grill is genuinely complete,
the low count is skillful look-it-up-first, not a shortcut."* Without it, a
well-done <6 grill is wrongly blocked. Always emit this signal after the last
question, before the §4 Write.

## 4.0 WRITE brief.md INLINE

The orchestrator writes `brief.md` directly — no writer subagent. The grill
(§3) already read every source doc to ground its recommended answers
(`product.md`, `purpose.md`, `tech-stack.md`, the discovered `CONTEXT_PATHS`),
so there is no context isolation to gain from a subagent, and a brief is a
~1-page scaffold fill — mechanical, not a large generated surface. Writing
inline removes a dispatch round-trip + a result-block parse + the
stop-without-a-result-block failure mode that seam carried.

1. Read `${CLAUDE_PLUGIN_ROOT}/templates/brief-scaffold.md`. You fill THIS
   skeleton; do not invent your own structure. The `## Section` headings are
   machine anchors (consumed verbatim by spec-planner) — keep them ASCII; fill
   only the body, in any language.
2. **Intersect Out-of-Scope with `purpose.md`** before writing. Read
   `conductor/purpose.md` if present; its Out-of-Scope boundaries are settled
   project exclusions — this Brief's Out-of-Scope must not contradict them
   (narrow this Brief's exclusions to fit, never widen past purpose.md).
3. Fill the scaffold from the §3 `USER_ANSWERS` (the primary truth), with
   project context as supporting/confirming material — never override what the
   user explicitly stated. Substitute `{Track Title}`, `{TRACK_ID}`,
   `{TRACK_TYPE}`, and today's date into frontmatter + H1; replace every
   section's guidance comment with real content.
   - **Do not fabricate.** An honest `"None identified."` under Open Questions
     or Stakeholders is correct; an invented stakeholder or constraint is a
     violation.
   - **References** = the union of (a) §2 discovered `CONTEXT_PATHS`,
     (b) any user-named files/URLs captured as `USER_REFERENCES` during §3,
     (c) the scaffold's default project links. List paths only.
4. Use the **Write tool** to write `{track_dir}/brief.md`.

## 4.1 VALIDATE

Re-read `{track_dir}/brief.md` and confirm it has the load-bearing structure
spec-planner depends on:

1. Frontmatter contains `track_id:` and `provenance:`.
2. The `## Out of Scope` heading is present (the one section that must be explicit, not inferred).

If either is missing → **halt**: `"brief.md failed validation (missing
<frontmatter|## Out of Scope>) — re-read ${CLAUDE_PLUGIN_ROOT}/templates/brief-scaffold.md
and rewrite {track_dir}/brief.md conforming to the scaffold, then re-validate."`
You wrote it inline, so you fix it inline — there is no writer to re-dispatch.

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
4. **Commit brief.md scoped** (never `git add -A`). `brief.md` is durable planning
   input living in the committed `conductor/tracks/<id>/` tree; an uncommitted brief
   is a resume hazard — a session clear or worktree switch would lose it. Stage
   **only** the track dir's `brief.md` (plus the per-track `.conductor/` marker
   change that `brief-finalize` just made, which IS tracked — unlike root
   `/.conductor/`). Same scoped-staging discipline as `setup` §3.6; the
   `git diff --cached --quiet ||` guard makes it a no-op only if already committed:
   ```bash
   git add "<track_dir>/brief.md" "<track_dir>/.conductor/" \
     && git diff --cached --quiet \
     || git commit -m "docs(<track_id>): brief — grilled shared understanding"
   ```
   If the `.conductor/` marker was the only change and it's already gone (finalize
   deleted it), the `git add` of that path is a harmless no-op — brief.md alone
   carries the commit.
5. Print the hand-off:
   > **Brief ready at [brief.md](<track_dir>/brief.md).**
   > When ready to plan, run: `/conductor:new-track <track_id>`
   > It will auto-detect this Brief and use it as authoritative planning input (skipping its own Q&A).

Do NOT auto-chain into `/conductor:new-track`. The hand-off is manual — the user may want to edit the Brief, sit on it, or hand it to someone else first.
