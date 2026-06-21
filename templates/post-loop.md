# Post-Loop Phases

Loaded by implement orchestrator when dispatch loop exits (action=finalize).

---

## 5.0 DEFERRED VERIFICATION

Resolve deferred tasks BEFORE finalization, so the quality score reflects the verified state.

```bash
track-state deferred-report "<track_dir>"
```

`count == 0` → skip. Otherwise present each deferred task via `AskUserQuestion`:
- "Verify completed" → `track-state complete "<track_dir>" --phase <p> --task <t> --sha ""`
- "Skip" → `track-state skip "<track_dir>" --phase <p> --task <t> --reason 'User verified not needed'`
- "Defer" → no action

After → `track-state sync-plan "<track_dir>"` + commit.

---

## 5.5 FINALIZATION

```bash
track-state finalize "<track_dir>"
track-state sync-plan "<track_dir>"
track-state registry-update "<track_dir>" "conductor/tracks.md"
```

Commit: `chore(conductor): Complete track '<desc>'`.

---

## 6.0 DOC SYNC

Dispatch `conductor:doc-syncer`. Prompt: `TRACK_DIR={track_dir} TRACK_ID={track_id}`.

---

## 6.5 WIKI LINT

Dispatch `conductor:doc-linter`. Prompt: `PROJECT_DIR={project_root}`.

Parse `---DOC LINT RESULT---` block:
- STATUS: PASS → announce "Wiki health check passed."
- STATUS: WARN → present findings, recommend running `/conductor:wiki query <topic>` to investigate.
- STATUS: FAIL → present findings, recommend manual review before archiving.

If STATUS: FAILURE (agent error) → announce and continue (non-blocking).

---

## 7.0 AUTO-REVIEW

1. Get SHA range: `track-state shas "<track_dir>"`
   If `count == 0` → skip review. Otherwise use the `range` field (`{first}~1..{last}`) — it includes the first commit's own changes; do NOT rebuild `{first}..{last}` yourself.
2. Dispatch `conductor:code-reviewer`. Description: `"Auto-review track '<desc>'"`.
   ```
   TRACK_DIR={track_dir}
   TRACK_ID={track_id}
   REVISION_RANGE={range}
   PRODUCT_GUIDELINES={resolved_path}
   TECH_STACK={resolved_path}
   STYLEGUIDES_DIR={resolved_path}
   ```
3. Parse `---REVIEW RESULT---` block:
   - Critical/High → **CHANGES REQUESTED** → offer to apply fixes or halt.
   - Medium/Low → **APPROVE WITH COMMENTS** → continue.
   - No issues → **APPROVE**.
   - STATUS: FAILURE (agent error) → announce and continue (non-blocking).
4. If "Apply Fixes" → these are **post-review patches, not plan tasks**. Dispatch ONE free-form patch agent (`Agent`, `subagent_type: "general-purpose"`):
   ```
   Apply the review findings in {TRACK_DIR}/.conductor/review-result.json.
   For each finding, apply its `suggestion`, then commit it separately as
   `fix(<area>): <finding title>` (<area> = code area touched). Run the test
   suite after applying and fix any regressions.
   ```
   This agent is NOT a plan task: do NOT pass `PHASE`/`TASK`, do NOT call
   `dispatch-finalize`, and do NOT modify `track-state.json` — the track is
   already finalized and these are remediation commits on top of it. After
   return, verify the commits landed (`git log --oneline -<count>`).

---

## 8.0 CLEANUP & ARCHIVE

Present options via `AskUserQuestion`:

> "Track '<track_id>' is complete. Choose cleanup action:"

Options:
- **Archive** (recommended): `track-state archive "<track_dir>"` + `registry-update` + commit
- **Keep Active**: no action
- **Delete**: confirm then `rm -rf "<track_dir>"` + remove from tracks.md + commit
