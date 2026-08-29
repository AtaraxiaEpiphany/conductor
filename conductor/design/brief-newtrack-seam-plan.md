---
type: concept
sources:
  - skills/brief/SKILL.md
  - skills/new-track/SKILL.md
  - scripts/track_state/brief.py
  - runtime/contracts/grill-discipline.md
last_verified: 2026-08-28
---

# Brief → New-Track Seam: Confirm-Gate Chaining + Pending-Brief Detection

Plan for two changes at the brief→new-track seam, agreed via grill (frontier
rounds, 2026-08-28). Status: **SHIPPED 2026-08-29** — `bbeac33..91eead8`
(3 commits, 2727 tests green): Task 1 `pending-briefs` CLI, Task 2 new-track
§2.1 scan step, Task 3 brief §5 confirm-gate.

## Problem

1. **Manual halt.** `/conductor:brief` §5 ends by halting and printing
   *"When ready to plan, run: `/conductor:new-track <track_id>`"*. The user
   must re-invoke new-track by hand with the exact (date-stamped) track_id.
2. **Exact-id adoption only.** `/conductor:new-track` §2.1 adopts an existing
   track dir only when `$ARGUMENTS` is that exact bare track_id. Invoked with
   no args or a description, it derives a **new** dated id — an existing
   `brief.md` sitting in another track dir is never found (§2.2b checks only
   the just-derived dir), so the Brief is orphaned.

## Design decisions (grilled)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Automation shape at the seam | **Confirm-gate** folded into brief §5's existing AskUserQuestion — not full auto-chain (brief Out-of-Scope is copied verbatim into spec.md; the edit window is the guard), not status-quo halt |
| D2 | When new-track scans for pending briefs | **Always**, before `derive-name`, whenever `$ARGUMENTS` is not an exact existing track_id (description args included) |
| D3 | Multiple pending briefs | **AskUserQuestion over candidates** (mirrors §0.5 resume semantics), 3 newest + "None — fresh track"; older ones named in the question text |
| D4 | Detection home | **New `track-state pending-briefs` subcommand** (code-owned glob; skill stays path-agnostic per §0.0) |
| D5 | After user edits the brief | **Re-ask once** ("Plan now / Done for now"); max 2 asks per run, usually 1 |
| D6 | >3 candidates | 3 newest + None option (AskUserQuestion 4-option cap) |

### Candidate rule (state partition)

A track dir is a **pending brief** iff:

- `brief.md` present, **and**
- `track-state.json` absent (state exists → revision/re-plan lane, §2.3
  existing-plan guard owns it), **and**
- `.conductor/brief-progress.json` marker absent (marker present →
  `brief-resume` lane owns it).

One state, one owner; overlapping detectors would double-offer.

### Invariants

- **Durability before consumption:** on "Plan now", brief-finalize + scoped
  commit run **before** new-track is invoked (uncommitted brief + session
  clear = resume hazard).
- **Single-homing:** the scan prose lives only in new-track §2.1; brief §5
  knows nothing about detection. The confirm-gate prose lives only in brief
  §5.
- **Idempotency (inherited):** an adopted dir goes through §2.1 step 5
  `new-track-init` (NT marker created) → §2.6 uniqueness check passes. No new
  mechanism.
- **Fail-open parsing:** a brief with unreadable H1/frontmatter is still a
  candidate (title degrades to the dir name).

## Tasks

### Task 1 — `track-state pending-briefs` CLI (commit 1)

Home: `scripts/track_state/brief.py`, sibling of `cmd_brief_resume`.

- `cmd_brief_pending()`: `_find_registry()` → glob `tracks/*/brief.md`; for
  each, apply the candidate rule (no `track-state.json`, no brief marker);
  emit JSON:
  `{action: "none"|"found", candidates: [{track_id, track_dir, title, brief_age_days}]}`,
  sorted newest-first by mtime; `title` from brief H1 (fail-open to dir name),
  `track_id` from brief frontmatter `track_id:` when parseable, else dir name.
  Always exit 0 (mirrors resume commands).
- Wire into `cli.py`: argument parser, help text.
- **Drift sites (all four — memory gotcha):** `_COMMAND_GROUPS`,
  `_SANCTIONED_TS_SUBCOMMANDS` (state-lock allowlist), **both**
  no-track-dir command lists, help text.
- Tests (`tests/test_brief_cli.py`): found / none / marker-excluded /
  state-excluded / newest-first sort / corrupt-frontmatter fail-open /
  no-registry. Note tmpdir tests around `CLAUDE_PROJECT_DIR` need try/finally
  restore.

**Acceptance:** `PYTHONPATH=. python3 -m pytest tests/test_brief_cli.py` green;
`track-state pending-briefs` prints `action` JSON on a scratch tree with all
three dir states.

### Task 2 — new-track §2.1 scan step (commit 2)

Home: `skills/new-track/SKILL.md` §2.1, new step between step 3
(propose-shape) and step 4 (derive-name).

- Run `track-state pending-briefs` when `$ARGUMENTS` is **not** a bare
  existing track_id (the existing-track-adoption case already covers that).
- Empty `$ARGUMENTS` → scan **before** the description AskUserQuestion (an
  adopted brief supplies the description).
- `found` → one AskUserQuestion: 3 newest candidates
  (*"Adopt '<title>' (<age>d)"*) + **None — fresh track**; >3 candidates
  named in the question text. Adopt → adopt track_id/track_dir, skip
  `derive-name`, §2.2b consumes the brief, `propose-shape` re-runs with
  `--brief`. None → existing flow unchanged.
- Tests (`tests/test_brief_wiring.py` `NewTrackConsumesBriefTests` or a new
  class): scan step present, ordered before derive-name, adoption ask + None
  option pinned, empty-args-before-description-ask pinned. Pinned assertIn
  phrases must sit contiguous on one line.

**Acceptance:** wiring tests green; full-suite run green.

### Task 3 — brief §5 confirm-gate (commit 3)

Home: `skills/brief/SKILL.md` §5.0 + frontmatter.

- Replace step 2's binary ask with a three-way AskUserQuestion:
  **Plan now — invoke `/conductor:new-track <track_id>` (Recommended)** /
  **Review/edit first** / **Done for now**.
- Review/edit → after the user finishes, **re-ask once** (Plan now / Done for
  now).
- Plan now → brief-finalize → scoped commit (unchanged step 4) → invoke
  `/conductor:new-track <track_id>` via the Skill tool. Add `Skill` to
  `allowed-tools`.
- Done for now → finalize + commit + print the current hand-off text (only
  this path prints it).
- Update §Flow line at the top ("Do NOT auto-chain" paragraph) to state the
  new contract: gated chain, edit window preserved, durability-before-invoke.
- Tests (`tests/test_brief_wiring.py`):
  - **Flip** `test_skill_does_not_autochain_new_track` →
    `test_skill_confirm_gated_invoke`: assert "Plan now" gate present; assert
    finalize + commit are ordered before the invoke; assert the "Done for
    now" path still prints the hand-off; assert `Skill` in allowed-tools.
- **Smoke-verify** the Skill tool is invocable from inside skill execution
  (allowed-tools gating untested for `Skill`; new-track §2.7's
  *"invoke `/conductor:implement`"* is the prose precedent). If the harness
  denies it, fall back to announcing the invocation instruction — do not
  ship a silent failure.

**Acceptance:** flipped + new wiring tests green; manual smoke:
`/conductor:brief` on a scratch track reaches the three-way ask; "Plan now"
invokes new-track which adopts the brief-bearing dir without an id.

### Task 4 — post-ship bookkeeping (no commit / memory only)

- Update the session memory campaign file: design reversal (manual hand-off →
  confirm-gate), the state-partition rule, new-subcommand drift sites hit.
- If the choice proves load-bearing later (a full-auto request resurfaces),
  append a `decision-*.md` record per grill-discipline §7's three-part test.

## Commit plan

1. `feat(conductor): pending-briefs — code-owned orphan-brief detection for new-track adoption`
2. `feat(conductor): new-track pre-derive brief scan — adopt pending briefs, no exact-id round-trip`
3. `feat(conductor): brief confirm-gate — plan-now chains into new-track, edit window preserved`

Each commit ships green (current baseline 2714 tests,
`PYTHONPATH=. python3 -m pytest`).

## Risks / gotchas

- **Reversal discipline:** the manual hand-off was deliberate and
  test-pinned; commit 3 flips the pin **and** the rationale prose in the same
  commit — a stale "manual by design" test comment would lie about the
  system.
- **AskUserQuestion caps:** 4 options max — candidate list capped at 3 + None.
- **One-question-per-round carries:** the adoption ask is one round; the
  confirm-gate is one round (+1 re-ask max) — interaction budget respected
  (grill-discipline §1: over-asking is a tax).
- **`pending-briefs` is registry-anchored** (`_find_registry()`), so
  non-default layouts resolve correctly; the skill never restates the tracks
  path.
