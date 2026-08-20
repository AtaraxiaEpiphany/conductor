"""Command-surface registry — the single source for the track-state CLI's
grouped command names and every set derived from them.

Deliberately a LEAF module: stdlib only, zero ``track_state`` sibling imports
and zero filesystem access, so anything in the dependency graph — cli.py's
help rendering, the pre-command rm/mv guard's sanctioned-subcommand allowlist,
the README sync generator — can import (or file-load) it without cycles or
import-chain cost. A new subcommand must be added to ``COMMAND_GROUPS`` here
first: absent from this table it is invisible in grouped help AND unsanctioned
for the pre-command guard's Layer A allowlist.
"""

COMMAND_GROUPS = [
    ("Lifecycle", ["init-from-plan", "start", "set-mode", "set-recovery-policy", "set-workflow-shape", "finalize", "archive"]),
    ("Navigation", ["next", "dispatch-next", "recover", "indices"]),
    ("State Mutations", ["lock", "complete", "fail", "skip", "defer", "block", "reset",
                         "set-max-retries", "split"]),
    ("Sync & Registry", ["sync-plan", "reconcile-plan", "sync-handoff",
                         "registry-update", "registry-add", "registry-doc"]),
    ("Handoff", ["get-handoff", "append-handoff", "harvest-candidates",
                 "compile-track-findings"]),
    ("Result Processing", ["write-result", "process-result"]),
    ("Dispatch Composites", ["dispatch-prepare", "dispatch-finalize", "record-summary"]),
    ("Rail B-min Spines", ["step", "post-loop-step", "post-loop-review",
                           "phase-verdict", "phase-checkpoint-review",
                           "skip-analyst-verdict", "skip-refute-review",
                           "failure-analyst-verdict", "phase-failure-analyst-verdict",
                           "amend-apply", "amend-clear",
                           "review-attest"]),
    ("Wave Parallelism", ["dispatch-wave", "wave-status", "wave-finalize", "wave-abort", "wave-step"]),
    ("Naming", ["derive-name", "propose-shape", "resolve-track", "check"]),
    ("New-Track Resume", ["new-track-resume", "new-track-init", "new-track-step",
                          "new-track-set-mode", "new-track-finalize"]),
    ("Brief", ["brief-resume", "brief-init", "brief-finalize", "brief-grill-done"]),
    ("Diagnostics", ["validate", "gc", "shas", "post-loop-status", "checklist-verify",
                     "deferred-report", "phase-done", "add-checkpoint", "preflight",
                     "quality-snapshot", "spec-integrity", "spec-anchors", "spec-delta",
                     "task-context", "view", "status"]),
    ("Workflow Studio", ["shape-studio", "registry-json", "registry-save"]),
    ("Logs", ["log-path", "subagent-log"]),
]

# The index-command family: subcommands whose positionals are phase/task/subtask
# indices rather than paths (they share the resolve_indices + zero-based-input
# repair preamble in ``cli.main``). Lives beside COMMAND_GROUPS so the whole
# command-surface story is one module.
INDEX_COMMANDS = frozenset({
    "lock", "complete", "fail", "skip", "block", "defer",
    "set-max-retries", "split",
})

# Subcommands the pre-command rm/mv guard sanctions (its Layer A allowlist):
# every grouped command plus the hidden "setup" alias (cli.py maps it to
# cmd_check) and "help". DERIVED — never hand-copied. The old ~90-entry literal
# in pre-command-check.py was a standing drift risk (subcommands have
# historically missed it), which is exactly why this module exists.
SANCTIONED_SUBCOMMANDS = frozenset(
    cmd for _group, cmds in COMMAND_GROUPS for cmd in cmds
) | {"setup", "help"}
