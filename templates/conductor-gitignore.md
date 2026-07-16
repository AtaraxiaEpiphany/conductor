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
# conductor:gitignore end
