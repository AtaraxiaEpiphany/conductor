# Post-Loop Phases

Loaded by implement orchestrator when dispatch loop exits (action=finalize).

---

## 5.0 FINALIZATION

```bash
track-state finalize "<track_dir>"
track-state sync-plan "<track_dir>"
track-state registry-update "<track_dir>" "conductor/tracks.md"
```

Commit: `chore(conductor): Complete track '<desc>'`.

---

## 5.5 DEFERRED VERIFICATION

```bash
track-state deferred-report "<track_dir>"
```

`count == 0` → skip. Otherwise present each deferred task via `AskUserQuestion`:
- "Verify completed" → `track-state complete --sha ""`
- "Skip" → `track-state skip --reason 'User verified not needed'`
- "Defer" → no action

After → `track-state sync-plan "<track_dir>"` + commit.

---

## 6.0 DOC SYNC

Dispatch `conductor:doc-syncer`. Prompt: `TRACK_DIR={track_dir} TRACK_ID={track_id}`.

---

## 7.0 AUTO-REVIEW

1. Get SHA range: `track-state shas "<track_dir>"`
   If `count == 0` → skip review.
2. Dispatch `conductor:code-reviewer`. Description: `"Auto-review track '<desc>'"`.
   ```
   TRACK_DIR={track_dir}
   TRACK_ID={track_id}
   REVISION_RANGE={first}..{last}
   PRODUCT_GUIDELINES={resolved_path}
   TECH_STACK={resolved_path}
   STYLEGUIDES_DIR={resolved_path}
   ```
3. Parse `---REVIEW RESULT---` block:
   - Critical/High → **CHANGES REQUESTED** → offer to apply fixes or halt.
   - Medium/Low → **APPROVE WITH COMMENTS** → continue.
   - No issues → **APPROVE**.
4. If "Apply Fixes" → dispatch `conductor:task-executor` for each fix. Process results normally.

---

## 8.0 CLEANUP & ARCHIVE

Present options via `AskUserQuestion`:

> "Track '<track_id>' is complete. Choose cleanup action:"

Options:
- **Archive** (recommended): `track-state archive "<track_dir>"` + `registry-update` + commit
- **Keep Active**: no action
- **Delete**: confirm then `rm -rf "<track_dir>"` + remove from tracks.md + commit
