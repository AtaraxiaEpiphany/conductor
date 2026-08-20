---
name: discover
description: Find recurring dev frictions worth making tracks for (read git log + dispatch-lifecycle.log + .conductor/ signals first), grill-triage them with the user, then write a proposals.md the user feeds to /conductor:brief one proposal at a time
when_to_use: User wants to decide WHAT to build next — surfacing recurring toil (manual rituals, retry storms, review debt, recovery spikes) as candidate tracks; produces conductor/discoveries/<date>-proposals.md for /conductor:brief to consume per accepted proposal
argument-hint: "[focus_area]"
allowed-tools: Bash, Read, Write, Grep, Glob, Agent, AskUserQuestion
model: sonnet
---

# Conductor Discover

**Discover** is the front door *before* specification: it finds the recurring dev
frictions worth making a track for, triages them with the user, and writes a
`proposals.md` — a **triage list, NOT a spec**. For each accepted proposal the
user runs `/conductor:brief <slug>` (the per-track grill), then `/conductor:new-track`.

**Flow:** `/conductor:discover` (this skill — read signals, grill-triage, write
proposals) → human picks which proposals to pursue → `/conductor:brief <slug>` per
proposal → `/conductor:new-track`. Do NOT auto-chain — the hand-off is manual, and
`brief` is a separate, deeper grill per track.

discover and brief divide the labor deliberately: **discover triages** (is this
loop real? is it worth a track? one-line value), **brief specifies** (goals,
out-of-scope, constraints, the 8-node decision tree). A proposal is a paragraph; a
brief is a page. Do not collapse them — triage in discover, specify in brief.

## 0.0 RESOLVE PATHS

- Project root: cwd. Verify it is a Conductor project (`conductor/` exists); else
  halt: *"Not a Conductor project — run /conductor:setup first."*
- `$ARGUMENTS` (optional): a `focus_area` seed (e.g. `tests`, `ci`, `auth`) that
  narrows the scan. Absent → scan everything.
- Discovery signals:
  - `.conductor/logs/dispatch-lifecycle.log` — dispatch/retry/failure patterns.
  - `.conductor/subagent-recovery-counters.json` — agents that recover/fail often.
  - `git log` — recent commits (manual rituals, recurring fix classes, churn).
  - `conductor/tracks/` — tracks already in flight (duplicate guard).

CRITICAL: Validate every tool call. On failure → halt → announce.

## 1.0 DISCOVERY (read the signals FIRST)

The whole value is asymmetric knowledge — the four-quadrant Q3: **you can see
loops in the logs the user hasn't noticed.** Read before you ask (the
grill-discipline contract's look-it-up-first rule). Run these, then synthesize
candidate loops — **do NOT grill yet**:

1. **Dispatch friction** — `grep` `.conductor/logs/dispatch-lifecycle.log` for
   `retry`, `re-dispatch`, `FAILED`, `denied`, recovery spikes. A task or agent
   that retries or recovers repeatedly is toil begging for a track.
2. **Commit patterns** — `git log --oneline -n 100` (or `--since="2 weeks ago"`).
   Look for: repeated fix classes (`fix(auth)…`, `fix(test)…`), manual-ritual
   commits ("run checks", "regen", "bump"), and churn hotspots (the same files
   touched repeatedly).
3. **Recovery counters** — read `.conductor/subagent-recovery-counters.json`; an
   agent whose counter climbs is a reliability-track candidate.
4. **Duplicate guard** — `ls conductor/tracks/`; a friction already covered by an
   in-flight or archived track is not a new proposal (name it as a duplicate, drop
   it).

Collect 3–8 **candidate loops**, each as a one-line observed signal + the evidence
path (the log line, the commit, the counter). These start as YOUR-KNOWN /
USER-UNKNOWN — you saw it in the logs; the user may not have. The §3 grill confirms
which are real and worth a track.

## 2.0 FOUR-QUADRANT STANCE + GRILL DISCIPLINE (Read-on-demand)

discover grills at the **triage** depth (confirm reality + worth), not brief's
specification depth. The stance, the grill loop, the premise-challenge pass, and
the operationalize-unknowns rule are **single-homed**. Before the grill, Read
`${CLAUDE_PLUGIN_ROOT}/runtime/contracts/grill-discipline.md` **and follow it** —
that contract is the one home; this skill does not restate the discipline (a second
home drifts, per prose-style Bucket B).

Posture (contract §1): this is **full grill** — deciding what to build is
high-stakes (a wrong "build this" wastes a whole track), so the grill earns its
turn-cost. The grill's *scope* here is triage, not the 8-node brief tree.

## 3.0 GRILL TO TRIAGE

> **MUST — every question via `AskUserQuestion`, one round at a time.**
> Each round poses the frontier — the currently-unblocked decisions — in one
> `AskUserQuestion` call of at most 4 questions, recommended answer first, and
> waits for the answers before the next round. The full procedure (frontier
> rounds, look-it-up-first, recommended-answer-first with rationale) is in the
> contract you Read at §2.

Walk the candidate loops. Within one candidate the triage decisions are dependent
(Q2 only matters if Q1 confirms), so pose those **one question at a time**, alone;
across candidates they are independent — Q1 for several candidates is a frontier
round (batch them, ≤4 per call, recommended answer first):

1. **Is it real?** Show the observed signal + its evidence path; recommend
   confirm/drop. A loop the user doesn't recognize may be stale tooling, not toil —
   drop it.
2. **Is it worth a track?** A track is heavy (spec → plan → TDD). Recommend a
   threshold: recurring **≥3× AND not cheaply fixable inline** → worth a track; a
   one-off or a quick inline fix → drop or defer.
   - **Premise-challenge (contract §4):** if the candidate looks like it's solving
     the wrong *layer* — automating a ritual that should be *deleted*, or building
     a tool for a process that should *change* — pose the **one** bounded challenge
     before converging. Catching "automate the thing you should stop doing" here is
     the highest-leverage triage move.
3. **One-line value + slug.** For an accepted candidate, capture the value in one
   line and propose a `<slug>` (1–3 lowercase words) for the later
   `/conductor:brief <slug>` hand-off.

**Operationalize unknowns (contract §5):** if "is it worth a track?" turns on a
*decidable* unknown ("how often does this actually fire?", "is it already covered
by a test?"), restate it as a probe (grep the log for the count; grep the suite)
and **run the probe**, then triage on the result — don't guess. Unknowns that are
genuinely "ask the stakeholder" (a human decision, not an experiment) stay as open
notes on the proposal.

A candidate that survives Q1 + Q2 is an **accepted proposal**; one that doesn't is
**dropped** (record the drop reason — a rejected proposal is useful history, not
noise).

## 4.0 WRITE proposals.md

1. Get today's date and ensure the dir exists:
   ```bash
   date +%Y-%m-%d
   mkdir -p conductor/discoveries
   ```
   Write `conductor/discoveries/<date>-proposals.md` (a fresh dated file per run —
   not an append).
2. Structure (human-read — the user carries each accepted proposal into
   `/conductor:brief` manually):

   ```markdown
   ---
   date: <YYYY-MM-DD>
   focus: <focus_area, or "all">
   provenance: /conductor:discover — git log + dispatch-lifecycle.log + .conductor/ signals
   ---

   # Discovery Proposals — <date>

   ## Accepted (run `/conductor:brief <slug>` next)

   ### <slug> — <one-line value>
   - **Signal:** <observed friction + evidence path>
   - **Why a track:** <recurring ≥N× / not cheaply fixable inline>
   - **Open notes:** <any stakeholder-decision unknown, or "none">
   - **Next:** `/conductor:brief <slug>`

   ### <slug> — …

   ## Dropped (for the record)

   - <candidate> — <drop reason>
   ```

3. **Do not fabricate** loops the signals didn't show. An honest `## Accepted` with
   zero entries (everything dropped as one-offs or wrong-layer) is correct — a
   discover run that finds nothing worth a track is a successful run.

## 4.1 COMMIT proposals.md SCOPED

`proposals.md` is durable planning input in the committed `conductor/discoveries/`
tree; an uncommitted proposal is lost on a session clear or worktree switch. Stage
**only** the new file (never `git add -A`):
```bash
git add "conductor/discoveries/<date>-proposals.md" \
  && git diff --cached --quiet \
  || git commit -m "docs(discover): proposals — <N> accepted, <M> dropped"
```

## 5.0 HAND-OFF

1. Announce: *"Proposals written to `conductor/discoveries/<date>-proposals.md` —
   <N> accepted, <M> dropped."*
2. Print one hand-off line per accepted proposal:
   > **`<slug>`** — <one-line value>. When ready: `/conductor:brief <slug>`.
3. Do NOT auto-chain into `/conductor:brief`. The user picks which proposal to
   specify first (or none). discover's job ends at the proposals file — it is
   discovery, not specification.
