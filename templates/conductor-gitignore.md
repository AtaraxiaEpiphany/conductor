# conductor:gitignore begin
# Conductor runtime scratch — two kinds, both under the root-anchored /.conductor/:
#   1. /conductor:wiki-doctor transient diagnostics (wiki-lint-findings-*.json,
#      wiki-diff-findings-*.json, wiki-diff-report.md).
#   2. Project-scoped hook telemetry — logs/ (subagent-failures, result-recovery,
#      override-audit, session-lifecycle, etc.) written by conductor's hooks via
#      lib.env.get_data_dir (CLAUDE_PROJECT_DIR/.conductor by default).
# Root-anchored (leading /) so per-track scratch — conductor/tracks/*/.conductor/,
# which is committed by track commits — is NOT ignored.
/.conductor/

# Conductor track-state scratch — written into every conductor/tracks/<id>/
# (the track ROOT, not under .conductor/). The root-anchored /.conductor/ rule
# above cannot reach them, and the per-track .conductor/.gitignore only governs
# .conductor/. Specific names — NOT *.lock/*.bak globally (collateral: yarn.lock,
# Cargo.lock, poetry.lock must stay committable). *.json.bak* also covers the
# registry <name>.json.bak written by track-state registry-studio saves, and the
# track-state.json.bak2/.bak3 litter an improvising orchestrator can hand-copy
# (one .bak is by design; the numbered chain is not, and `git add -A` would
# sweep it into a commit).
*.json.bak*
.track-state.lock
.track-state.json.tmp.*

# Common build artifacts / dependency caches — a safety net so task-executor's
# `git add -A` (Step 8) cannot sweep these into an implementation commit. These
# are sensible defaults for most stacks; review and adjust for your project
# (e.g. remove `build/` if a committed `build/` dir is intentional).
node_modules/
dist/
build/
out/
__pycache__/
*.pyc
.venv/
venv/
coverage/
.cache/
.next/
.nuxt/
.turbo/
target/
*.log
.DS_Store
Thumbs.db
# conductor:gitignore end
