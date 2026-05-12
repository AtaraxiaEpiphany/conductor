# Scripts Directory

This directory contains all Conductor hook scripts and utilities.

## Architecture

All scripts are pure Python (Python 3.8+) with a shared library architecture
to eliminate code duplication and improve maintainability.

## Shared Library (`lib/`)

The `lib/` directory contains reusable modules:

| Module | Purpose |
|--------|---------|
| `hook_io.py` | Hook JSON input/output handling per Claude Code protocol |
| `logging.py` | Log directory initialization and entry writing |
| `env.py` | Environment variable utilities |
| `json_utils.py` | JSON loading/saving with safe defaults |
| `path_utils.py` | Path and directory operations |
| `validation.py` | Validation functions for state and inputs |
| `git_utils.py` | Git operation utilities |

## Hook Scripts

| Script | Hook Event | Purpose |
|--------|------------|---------|
| `filter-subagent-output.py` | PostToolUse (Agent) | Filter subagent output to main context; failure/recovery detection |
| `git-notes-query.py` | CLI | Query git notes audit data |
| `lint-track-state.py` | CLI | Boundary enforcement linter (F1, F4) |
| `on-batch-complete.py` | PostToolBatch | Batch-level validation |
| `on-compact.py` | PreCompact | Compression priority instructions |
| `on-phase-checkpoint-stop.py` | Stop (phase-checker) | Phase checkpoint logging |
| `on-review-stop.py` | Stop (code-reviewer) | Code review logging |
| `on-subagent-start.py` | SubagentStart | Subagent execution reminders |
| `on-subagent-stop.py` | SubagentStop | Subagent completion logging |
| `on-test-run.py` | PostToolUse (Bash) | Test monitoring and TDD context |
| `pre-command-check.py` | PreToolUse (Bash) | Command execution protection |
| `session-end.py` | SessionEnd | Session cleanup and metrics |
| `session-start.py` | SessionStart | Session initialization |
| `state-consistency-check.py` | Stop (implement) | State consistency guard |

## Testing

Run the test suite to validate all scripts:

```bash
python3 scripts/test-all.py
```

## Migration History

Original Bash scripts are backed up in `.backup/bash-originals/`.

Migration completed: 2026-05-11 (commits 7158b06, 785a2bb)
