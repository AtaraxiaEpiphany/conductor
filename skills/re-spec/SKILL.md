---
name: re-spec
description: Mid-track re-plan/re-spec after git reset — edit spec.md (AC/constraint/workflow), surface which completed SHAs a changed AC puts at risk, re-validate, commit, then hand off to /conductor:reconcile
when_to_use: User did a git reset --hard to undo divergent work, then wants to re-spec the remaining work — change an Acceptance Criterion, add a constraint, reword an FR, or inject workflow guidance for the remaining tasks — before continuing. This is the spec.md half; /conductor:reconcile is the plan.md/state half. Use THIS when the user is editing spec.md (not just plan.md).
argument-hint: "[track] [--add-constraint \"...\"] [--edit-ac AC-n \"...\"]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
model: sonnet
---

# Conductor Re-Spec (mid-track spec.md editing after a reset)

## 1.0 SYSTEM DIRECTIVE

You are a teleoperator for the **spec.md half** of post-reset recovery. The user ran `git reset --hard` to undo divergent work and now wants to **edit `spec.md`** — change an Acceptance Criterion, add a constraint, reword an FR, or inject workflow guidance for the remaining tasks — *before* continuing the track.

Your sibling `/conductor:reconcile` owns the **plan.md ↔ track-state.json** half (name-keyed, SHA-preserving). You own the **spec.md** half. The two compose: re-spec first (this skill), then `/conductor:reconcile` if the spec change also needs plan.md structural edits.

**Core Protocols:** State Lock (F1). Resolve the track dir via the project CLAUDE.md TOC or `track-state resolve-track "<track>"`.

**What you DO own:** editing `spec.md` prose, appending to `.conductor/track-directives.md` (the constraint/workflow channel), and making **one scoped commit** (`git add spec.md && git commit -m "docs(spec): …"`).

**What you do NOT own:** `track-state.json` or `plan.md` (that's reconcile — never touch them here), and **never auto-reset completed work** even when a spec change invalidates it (see §5).

## 2.0 WHEN TO USE THIS (vs. the other post-reset paths)

| Situation | Skill |
|---|---|
| Editing `spec.md` mid-track (AC/constraint/FR/workflow) after a reset | **`re-spec`** (this skill) |
| Editing `plan.md` mid-track (tag/split/reorder) after a reset, keep SHAs | `/conductor:reconcile` |
| Git-revert completed commits + reset state | `/conductor:revert` |
| Fresh start, wipe all progress | `track-state init-from-plan --force` (destroys SHAs — rare) |

The load-bearing difference: a changed **Acceptance Criterion can silently invalidate a completed task's SHA** — the task claimed `<!-- AC-3 -->` and carries `commit_sha=abc1234`, but the new AC-3 is stricter. `reconcile` won't catch this (it reconciles *structure*, not *meaning*). **This skill surfaces it (§5) and leaves the keep-vs-reset decision to the user — never auto-reset.**

## 3.0 PROCEDURE

### 3.1 Resolve the track
```
track-state resolve-track "<track>"     # confirm the track dir
```

### 3.2 Snapshot before
Save a copy of the current `spec.md` (you need a baseline for the blast-radius diff in §5):
```
cp spec.md /tmp/spec-before-$$.md
```
Then confirm the spec is well-formed before you touch it:
```
track-state spec-anchors "<track>"     # must be ok:true before you edit
```
If `spec-anchors` is already failing, **halt** — fix the existing spec defect before adding to it; re-spec is not the place to paper over a malformed spec.

### 3.3 Edit spec.md (three modes — combine freely)

Parse `$ARGUMENTS` for the structured flags; fall back to free-edit when none are present.

**Mode A — `--add-constraint "<text>"` (repeatable):** append a bullet under `## Constraints`. If the heading is absent, create it (above `## Out of Scope`, per `templates/spec-scaffold.md`). **Then also append the constraint to `.conductor/track-directives.md`** (the directive channel — see §3.3b): this is what makes the constraint live to future dispatches.

**Mode B — `--edit-ac AC-n "<new text>"` (repeatable):** rewrite that AC bullet's body in place. Match the line with `^-\s+AC-<n>\s*[:\-]?\s*.*$` and replace the body after the ID. Keep the `- AC-N:` prefix byte-identical (the parser keys on it — `spec_parse._AC`). One edit per AC.

**Mode C — free-edit (no flags):** the user has already hand-edited `spec.md` (or will, by hand mid-skill). You still run §3.4–§7 to commit, assess blast-radius, and re-validate. If the working-tree `spec.md` is unchanged from the §3.2 snapshot AND no flags were given, **halt and ask** what the user wants changed — don't invent an edit.

#### 3.3b The directive channel (`.conductor/track-directives.md`)
For every `--add-constraint` (and any future `--directive "<section>:<text>"`), **also** append a line to `{TRACK_DIR}/.conductor/track-directives.md`. Format:
```markdown
## Constraints
- must stay < 200ms p99 latency under load
```
Create the file if absent; append under the matching `## <section>` heading, creating the heading if needed. This file lives under `.conductor/` so conductor's existing commit staging already sweeps it into bookkeeping commits — **do not** give it its own commit; it rides the next conductor commit. *(Read-side wiring into task-executor Layer 0 is a follow-up; for now this is the canonical persistence site so constraints aren't lost.)*

### 3.4 Commit the spec edit explicitly
```
git add spec.md
git diff --cached --quiet || git commit -m "docs(spec): <one-line summary of what changed and why>"
```
Run with `cwd = <track_dir>`. **Scoped to `spec.md` only** — never `git add -A`, never the conductor staging set. Conductor's bookkeeping commit deliberately stages only `track-state.json`/`plan.md`/`.conductor/`; a spec edit needs its own `docs(spec):` commit so it's not stranded in the working tree. Fail-open: if this isn't a git track, warn and continue (the spec edit still applies; it just won't be committed).

If nothing is staged after `git add spec.md` (the edit was a no-op), skip the commit and warn the user.

### 3.5 Blast-radius report (the headline step)
```
track-state spec-delta "<track>" --before /tmp/spec-before-$$.md
```
Read the JSON. The `at_risk_tasks` array lists **completed tasks (with `commit_sha`) whose claimed AC was edited** — SHAs that may no longer satisfy the new AC. For each, report to the user verbatim:

> ⚠ AC-3 changed ("handle 1k" → "handle 100k"). Completed tasks claiming it:
>   - P2.T1 "impl concurrency" (sha abc1234)
>   - P3.T2 "add load shedding" (sha def5678)
> These SHAs may no longer satisfy the new AC. To redo the work: `track-state reset task --phase <P> --task <T>` (destroys the SHA) or `/conductor:reconcile --clear-dangling "<P>:<T>"`. To keep as-is, do nothing.

**Surface only. Do NOT run reset. Do NOT run reconcile --clear-dangling.** Wait for the user to decide. This mirrors reconcile's refuse-on-ambiguity: the SHA is the user's to keep or destroy, never the skill's.

If `at_risk_tasks` is empty, say so plainly and continue — the spec change put nothing at risk.

### 3.6 Re-validate
```
track-state spec-anchors "<track>"     # must be ok:true
track-state spec-integrity "<track>"   # advisory — read the rates, don't block on N/A
```
If `spec-anchors` now reports `ok:false` (e.g. a free-edit deleted the `## Acceptance Criteria` heading or mangled an `AC-N:` bullet), **halt immediately**: "spec.md is now missing machine anchors the parser needs — the edit broke the structure. Fix the edit and re-run." Do not leave the track in a state where `parse_spec` silently degrades to `N/A`.

### 3.7 Hand off to /conductor:reconcile (conditional)
If the spec change implies **plan.md** structural edits — the user also edited `plan.md`, or the blast-radius shows new ACs that need new tasks, or removed ACs whose tasks should go — instruct the user:

> The spec change may need plan.md structural edits. After you've made them, run `/conductor:reconcile "<track>"` to sync plan.md ↔ track-state.json by name (SHAs preserved).

You do **not** edit plan.md or call reconcile yourself — that's reconcile's invariant ("the CLI owns the write").

## 4.0 GUARDRAILS

- **Never auto-reset completed work.** A changed AC may invalidate a SHA, but the SHA is the user's to keep or destroy. Surface via `spec-delta`, offer the exact reset command, wait. This is the headline discipline.
- **Scoped commit, never global staging.** `git add spec.md` only — never `-A`, never extend conductor's staging set. A spec edit gets exactly one `docs(spec):` commit.
- **spec-anchors failure = halt.** Never leave the track with a malformed spec (parser degrades silently to N/A). Re-validate after every edit; halt on `ok:false`.
- **Don't invent edits.** If no flags and no working-tree change, ask what the user wants changed rather than guessing.
- **Constraints need the directive channel.** A new `## Constraints` entry is dead text to every machine check (`spec_parse` ignores that section). Always mirror `--add-constraint` into `.conductor/track-directives.md` so it's at least persisted at the canonical site; the read-side wiring into task-executor is the follow-up campaign.
- **re-spec ≠ reconcile.** Don't touch `plan.md` or `track-state.json`. If the user wants plan changes, hand off to `/conductor:reconcile` after you're done with spec.
