"""CLI entry point: argument parsing and command routing."""
import json
import sys
from pathlib import Path

from .core import load
from .constants import EXECUTION_MODES
from .helpers import out, flag
from .mutations import cmd_lock, cmd_fail, cmd_skip, cmd_block, cmd_defer
from .cmd_complete import cmd_complete
from .dispatch import (
    cmd_next, cmd_dispatch_next, cmd_dispatch_prepare, cmd_dispatch_finalize,
    cmd_recover, cmd_step, cmd_post_loop_step, cmd_post_loop_review,
    cmd_phase_verdict, cmd_phase_checkpoint_review,
    cmd_skip_analyst_verdict, cmd_skip_refute_review,
)
from .result import cmd_process_result, cmd_write_result
from .validate import cmd_validate
from .quality import cmd_init_from_plan, cmd_start, cmd_set_mode, cmd_finalize, cmd_archive, cmd_gc, cmd_checklist_verify
from .misc import (
    cmd_reset, cmd_indices, cmd_shas, cmd_add_checkpoint,
    cmd_deferred_report, cmd_phase_done, cmd_registry_update, cmd_registry_add,
    cmd_record_summary, cmd_preflight, cmd_quality_snapshot,
    cmd_spec_integrity, cmd_derive_name, cmd_post_loop_status,
    cmd_resolve_track, cmd_check, _resolve_track_dir_or_halt,
)
from .handoff import cmd_get_handoff, cmd_sync_handoff, cmd_append_handoff, cmd_harvest_candidates
from .sync import cmd_sync_plan
from .wave import (
    cmd_dispatch_wave, cmd_wave_status, cmd_wave_finalize, cmd_wave_abort,
    cmd_wave_step,
)
from .new_track import (
    cmd_new_track_init, cmd_new_track_step, cmd_new_track_set_mode,
    cmd_new_track_resume, cmd_new_track_finalize,
)


_BOOL_FLAGS = {"--full", "--fix", "--check", "--force"}

# Commands EXCLUDED from short-id resolution (their ``<track-dir>`` positional
# is not "an existing track to locate"):
#   * bootstrap/creation (``init-from-plan`` / ``new-track-*``): destination
#     path for a not-yet-registered track — ``new-track-init`` even creates the
#     dir, so ``is_dir()`` is False and a bare id would mis-resolve to
#     ``no_match``, breaking creation;
#   * ``preflight``: a diagnostic whose contract is "always exit 0 and diagnose
#     the raw path" (is it a file? the conductor root? missing state?) —
#     resolution's "exit 1 on unresolvable" would break that. ``check`` already
#     does resolve + preflight in one call for the short-id case.
# Every OTHER command with a <track-dir> positional resolves a bare track_id /
# shortname through ``_resolve_track_dir_or_halt`` (existing-path fast path
# skips resolution), so ``track-state next auth``, ``indices auth``,
# ``validate auth`` all work. See ``_resolve_track_dir_or_halt``.
_TD_NO_RESOLUTION_COMMANDS = {
    "init-from-plan", "new-track-init", "new-track-step",
    "new-track-set-mode", "new-track-finalize", "preflight",
}


def positional(args):
    """Extract positional args, skipping flags and their values."""
    result = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            if "=" in a:
                continue
            if a not in _BOOL_FLAGS:
                skip = True
            continue
        result.append(a)
    return result


def _fix_zero_based_input(track_dir, pi_str, ti_str, si_str):
    """Detect likely 0-based usage and auto-convert to 1-based.

    Indices are 1-based throughout the system. This detects the common
    off-by-one mistake of passing 0-based indices and corrects it,
    printing a warning to stderr.
    """
    try:
        pi = int(pi_str)
        ti = int(ti_str) if ti_str is not None else None
    except (ValueError, TypeError):
        return pi_str, ti_str, si_str

    state_path = Path(track_dir) / "track-state.json"
    if not state_path.exists():
        return pi_str, ti_str, si_str

    try:
        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return pi_str, ti_str, si_str

    phases = state.get("phases", [])
    n_phases = len(phases)
    phase_fixed = False

    if n_phases > 0 and pi == 0:
        pi = 1
        pi_str = str(pi)
        phase_fixed = True

    if ti is not None and phase_fixed:
        phase = phases[pi - 1] if 1 <= pi <= n_phases else None
        if phase:
            tasks = phase.get("tasks", [])
            n_tasks = len(tasks)
            if n_tasks > 0 and ti == 0:
                ti = 1
                ti_str = str(ti)

            if si_str is not None:
                try:
                    si = int(si_str)
                except (ValueError, TypeError):
                    si = None
                if si is not None and si == 0 and 1 <= ti <= n_tasks:
                    subs = tasks[ti - 1].get("subtasks", [])
                    n_subs = len(subs)
                    if n_subs > 0:
                        si = 1
                        si_str = str(si)

    if phase_fixed:
        print(f"WARNING: Auto-converted 0-based indices to 1-based: "
              f"phase={pi}, task={ti_str}"
              + (f", subtask={si_str}" if si_str is not None else ""),
              file=sys.stderr)

    return pi_str, ti_str, si_str


def resolve_indices(pos, args):
    """Resolve phase/task/subtask from both positional args and named flags.

    Named flags (--phase, --task, --subtask) take priority over positional.
    Also accepts --phase-index/--task-index/--subtask-index as aliases.
    Returns (phase, task, subtask) — subtask may be None.
    """
    pi = (flag(args, "--phase")
          or flag(args, "--phase-index"))
    ti = (flag(args, "--task")
          or flag(args, "--task-index"))
    si = (flag(args, "--subtask")
          or flag(args, "--subtask-index"))

    if pi is None and len(pos) >= 1:
        pi = pos[0]
    if ti is None and len(pos) >= 2:
        ti = pos[1]
    if si is None and len(pos) >= 3:
        si = pos[2]

    return pi, ti, si


# Rendered once from EXECUTION_MODES so help text can't drift from the enum.
_EXEC_MODE_CHOICES = "|".join(EXECUTION_MODES)

COMMAND_HELP = {
    "init-from-plan": ("init-from-plan <track-dir> --track-id <id>\n"
                       "                  --type <feature|bugfix|chore|docs> --description <text>\n"
                       f"                  [--execution-mode <{_EXEC_MODE_CHOICES}>] [--check] [--force]",
                       "Create track-state.json by parsing plan.md (refuses to overwrite; --force re-bootstraps)"),
    "start": ("start <track-dir>",
              "Transition track from 'new' to 'in_progress'"),
    "set-mode": (f"set-mode <track-dir> --mode <{_EXEC_MODE_CHOICES}>",
                 "Switch execution_mode on an existing track (no re-init)"),
    "next": ("next <track-dir> [--full]",
             "Find the next task to execute (compact JSON by default; --full for complete envelope)"),
    "dispatch-next": ("dispatch-next <track-dir> [--full]",
                      "One-call dispatch: next + parent-complete + tag routing"),
    "recover": ("recover <track-dir> [--full]",
                "Recover current task after interruption (auto-fixes state, advances past terminal)"),
    "lock": ("lock <track-dir> <phase> <task> [<subtask>]\n"
             "       lock <track-dir> --phase <n> --task <n> [--subtask <n>]",
             "Set task/subtask status to in_progress"),
    "complete": ("complete <track-dir> <phase> <task> [<subtask>] --sha <sha>\n"
                 "         complete <track-dir> --phase <n> --task <n> [--subtask <n>] --sha <sha>",
                 "Mark task completed with commit SHA"),
    "fail": ("fail <track-dir> <phase> <task> [<subtask>] --summary <text>\n"
             "     fail <track-dir> --phase <n> --task <n> [--subtask <n>] --summary <text>",
             "Mark task failed (increments retry_count)"),
    "skip": ("skip <track-dir> <phase> <task> [<subtask>] --reason <text>\n"
             "     skip <track-dir> --phase <n> --task <n> [--subtask <n>] --reason <text>",
             "Skip task with reason"),
    "defer": ("defer <track-dir> <phase> <task> [<subtask>] --reason <text>\n"
              "      defer <track-dir> --phase <n> --task <n> [--subtask <n>] --reason <text>",
              "Defer task for later verification"),
    "block": ("block <track-dir> <phase> <task> [<subtask>] --reason <text>\n"
              "      block <track-dir> --phase <n> --task <n> [--subtask <n>] --reason <text>",
              "Block task with reason"),
    "reset": ("reset <track-dir> --scope <task|phase|track> [--phase <n>] [--task <n>]",
              "Reset task(s) to pending, clearing completion fields and syncing plan"),
    "finalize": ("finalize <track-dir>",
                 "Transition track to completed/blocked/failed, compute quality score"),
    "archive": ("archive <track-dir> [--force]",
                "Archive a completed track (refuses unless doc-sync ran; --force to skip)"),
    "sync-plan": ("sync-plan <track-dir>",
                  "Sync plan.md checkbox markers from track-state.json"),
    "sync-handoff": ("sync-handoff <track-dir>",
                     "Regenerate handoff.md index from current state"),
    "get-handoff": ("get-handoff <track-dir> <phase> <task> [--subtask <n>]",
                    "Read handoff content for a specific task"),
    "append-handoff": ("append-handoff <track-dir> <phase> <task>\n"
                       "                  --type <explore|decision|risk|deviation>\n"
                       "                  --content '<json>' (or stdin) [--subtask <n>]",
                       "Append notes to a task's handoff file"),
    "harvest-candidates": ("harvest-candidates <track-dir>",
                           "Extract durable findings (graduation candidates + decisions) from handoffs for corpus-writer"),
    "registry-update": ("registry-update <track-dir> <tracks-md-path>",
                        "Update track entry in Tracks Registry (tracks.md)"),
    "registry-add": ("registry-add <track-dir> [<tracks-md-path>]",
                     "Append the canonical entry for a track to tracks.md (idempotent; auto-locates registry)"),
    "write-result": ("write-result <track-dir> --status success|failure --commit-sha <sha>\n"
                     "                                --summary <text> --coverage-pct <n> ...\n"
                     "                  <track-dir> [--data '<json>']   (or pipe JSON on stdin)",
                     "Write result.json from typed flags (no JSON), --data, or stdin"),
    "process-result": ("process-result <track-dir>",
                       "Read result.json, update state, sync plan, write git notes, enforce gates"),
    "dispatch-prepare": ("dispatch-prepare <track-dir> [--full]",
                         "Composite: next + lock + sync-plan + route (reduces round trips)"),
    "dispatch-finalize": ("dispatch-finalize <track-dir> [--override k=v,k2=v2] [--full]",
                          "Composite: process-result + conductor commit + sync-plan. --override patches empty result fields"),
    "step": ("step <track-dir> [--full]",
             "Rail B-min spine: composes recover + next + prepare + finalize into ONE leaf action "
             "(dispatch / ask / phase_checkpoint / skip_analyze / wave_active / done / error). "
             "Driven by skills/implement-step/SKILL.md; see conductor/design/rail-b-step.md."),
    "post-loop-step": ("post-loop-step <track-dir> [--full]",
                       "Rail B-min post-loop spine: collapses the prose post-loop (§5.0–§8.0) into ONE leaf "
                       "action (deferred_ask / finalize / dispatch / dispatch_advisory / dispatch_review / digest / "
                       "archive_ask / done / halt / error). Driven by skills/post-loop-step/SKILL.md."),
    "post-loop-review": ("post-loop-review <track-dir> --status <APPROVE|APPROVE_WITH_COMMENTS|CHANGES_REQUESTED|FAILURE> [--critical <N>] [--high <N>]",
                         "Stamp the reviewed-range sidecar from the code-reviewer STATUS (a real review stamps; "
                         "FAILURE does not → re-review). Also stamps the verdict + Critical/High counts "
                         "(--critical/--high, transcribed from the RESULT block) for audit. Owns the §7.0 "
                         "gate-advance in code, not teleoperator prose."),
    "phase-verdict": ("phase-verdict <track-dir> --ac-verdict <passed|warn|skipped|FAILED|ERROR> "
                      "[--ac-gate <gate>] [--ac-n-ungrounded <N>] --l1-status <passed|failed|error> --l1-command <cmd>",
                      "Transcribe the fanned ac-tracer + test-runner verdicts to the checkpoint marker "
                      "(stage=synth_pending); the next `step` emits the phase-checker synth dispatch. "
                      "Owns the §3.2 parse→assemble step in code, not teleoperator prose."),
    "phase-checkpoint-review": ("phase-checkpoint-review <track-dir> --status <PASSED|FAILED> [--sha <7-hex>] [--reason <text>]",
                                "Stamp the phase checkpoint from phase-checker's STATUS (PASSED stamps + clears; "
                                "FAILED clears → halt). Owns the §3.7 stamp/halt step in code, not teleoperator prose."),
    "skip-analyst-verdict": ("skip-analyst-verdict <track-dir> --recommendation <skip|pause_and_escalate|retry_with_modification> "
                             "[--reasoning <text>] [--impact <text>] [--can-skip <bool>]",
                             "Transcribe skip-analyst's recommendation to the skip-analysis marker (stage=analyzed); "
                             "the next `step` routes (skip→dispatch_refuter; pause/retry→halt). Owns the §3.6 route in code."),
    "skip-refute-review": ("skip-refute-review <track-dir> --status <SUSTAINED|REFUTED|FAILURE> [--reasoning <text>]",
                           "Transcribe the refuter's STATUS onto the skip-analysis marker (stage=refuted); the next `step` "
                           "routes (REFUTED/FAILURE→skip+advance; SUSTAINED→halt). Owns the §3.6 skip-refute in code."),
    "record-summary": ("record-summary <track-dir>",
                       "Record compact task summary (stdin JSON) for post-compaction recovery"),
    "dispatch-wave": ("dispatch-wave <track-dir> [--full]",
                      "Wave parallelism: fan out a ready-set of file-disjoint tasks into git worktrees "
                      "(opt-in; emits no_ready_tasks / wave_active / dispatch_wave)"),
    "wave-status": ("wave-status <track-dir> [--full]",
                    "Read-only view of the active wave ledger + member states"),
    "wave-finalize": ("wave-finalize <track-dir> <phase> <task> [--full]\n"
                      "                wave-finalize <track-dir> --phase <n> --task <n> [--full]",
                      "Integrate one worktree member: squash-merge its commit, run finalize transitions, "
                      "tear down the worktree (SUCCESS / FAILURE / conflict→fail)"),
    "wave-abort": ("wave-abort <track-dir>",
                   "Abort the active wave: reset in-flight members to pending, tear down worktrees, "
                   "delete the ledger (recovery for a wedged wave)"),
    "wave-step": ("wave-step <track-dir> [--full]",
                  "Rail B-min wave spine: composes dispatch-wave + wave-finalize into ONE leaf action "
                  "(dispatch_batch / wave_integrate / seam_review / serial / phase_checkpoint / "
                  "ask / skip_analyze / done / error). Driven by skills/parallel-step/SKILL.md; "
                  "see conductor/design/rail-b-wave-step.md."),
    "validate": ("validate <track-dir> [--fix]",
                 "Validate state; always reports auto-fix analysis, --fix persists repairs"),
    "gc": ("gc <track-dir>",
           "Garbage collection: clean orphaned artifacts, detect stale state"),
    "shas": ("shas <track-dir>",
             "List all commit SHAs from completed tasks"),
    "post-loop-status": ("post-loop-status <track-dir>",
                         "Read-only post-loop resumability gates: finalized / doc-synced / review-range"),
    "checklist-verify": ("checklist-verify <track-dir>",
                         "Check feature checklist verification status"),
    "deferred-report": ("deferred-report <track-dir>",
                        "List all deferred tasks with context"),
    "phase-done": ("phase-done <track-dir> <phase>",
                   "Check if all tasks in a phase are in terminal state"),
    "add-checkpoint": ("add-checkpoint <track-dir> <phase> <sha>",
                       "Add/update checkpoint SHA for a phase in plan.md"),
    "indices": ("indices <track-dir>",
                "Print phase/task/subtask index mapping for the track"),
    "preflight": ("preflight <track-dir>",
                  "Verify core track files (spec/plan/track-state.json) exist and load; ok:false if not"),
    "quality-snapshot": ("quality-snapshot <track-dir>",
                         "Read-only aggregate quality grades: completion, coverage, evidence gaps"),
    "spec-integrity": ("spec-integrity <track-dir>",
                       "Read-only AC coverage rates (TC/plan/verification) + advisory gate; FR/NFR counts"),
    "derive-name": ("derive-name <shortname>",
                    "Derive canonical track_id (<shortname>_<YYYYMMDD>) and track_dir for "
                    "today; idempotent. Uniqueness is the skill's job (new-track §2.6)."),
    "resolve-track": ("resolve-track [<query>] [--registry <path>]",
                      "Resolve a track_dir from conductor/tracks.md (exact id / shortname "
                      "prefix / auto-select the single active track). ALWAYS exits 0 — "
                      "switch on ok/reason (ambiguous→ask, no_registry→setup)."),
    "check": ("check [<query>] [--registry <path>]",
              "Resolve + preflight in one call — returns {action:proceed|ask|halt} "
              "with {td, track_id, status, via} or a precise halt reason "
              "(track_not_initialized/track_dir_missing/preflight/ambiguous/no_match/"
              "no_non_terminal/no_registry). ALWAYS exits 0 — the skill §1.0 single "
              "readiness step. ('setup' is kept as a hidden alias.)"),
    "new-track-init": ("new-track-init <track-dir> --track-id <id> --description <text> --type <feature|bugfix|chore|docs>",
                       "Write the new-track resume marker (idempotent — no-op if one exists)"),
    "new-track-step": ("new-track-step <track-dir> <spec_planned|reviewed|state_created|registry_updated>",
                       "Stamp a resume step done (idempotent, order-preserving)"),
    "new-track-set-mode": (f"new-track-set-mode <track-dir> --mode <{_EXEC_MODE_CHOICES}>",
                          "Write execution_mode into the new-track resume marker"),
    "new-track-resume": ("new-track-resume",
                         "Detect any interrupted new-track (committed:false marker) and emit its "
                         "resume directive. ALWAYS exits 0 — action:none|resume"),
    "new-track-finalize": ("new-track-finalize <track-dir>",
                           "Delete the new-track resume marker (track is durable; idempotent)"),
}

_COMMAND_GROUPS = [
    ("Lifecycle", ["init-from-plan", "start", "set-mode", "finalize", "archive"]),
    ("Navigation", ["next", "dispatch-next", "recover", "indices"]),
    ("State Mutations", ["lock", "complete", "fail", "skip", "defer", "block", "reset"]),
    ("Sync & Registry", ["sync-plan", "sync-handoff", "registry-update", "registry-add"]),
    ("Handoff", ["get-handoff", "append-handoff", "harvest-candidates"]),
    ("Result Processing", ["write-result", "process-result"]),
    ("Dispatch Composites", ["dispatch-prepare", "dispatch-finalize", "record-summary"]),
    ("Rail B-min Spines", ["step", "post-loop-step", "post-loop-review",
                           "phase-verdict", "phase-checkpoint-review",
                           "skip-analyst-verdict", "skip-refute-review"]),
    ("Wave Parallelism", ["dispatch-wave", "wave-status", "wave-finalize", "wave-abort", "wave-step"]),
    ("Naming", ["derive-name", "resolve-track", "check"]),
    ("New-Track Resume", ["new-track-resume", "new-track-init", "new-track-step",
                          "new-track-set-mode", "new-track-finalize"]),
    ("Diagnostics", ["validate", "gc", "shas", "post-loop-status", "checklist-verify",
                     "deferred-report", "phase-done", "add-checkpoint", "preflight",
                     "quality-snapshot", "spec-integrity"]),
]


def cmd_help(command=None):
    """Print usage help for all commands or a specific command."""
    if command and command in COMMAND_HELP:
        usage, desc = COMMAND_HELP[command]
        print(f"track-state {usage}\n\n  {desc}")
        return

    if command:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Run 'track-state help' to see all commands.", file=sys.stderr)
        sys.exit(1)

    print("track-state — Conductor track state management CLI")
    print()
    print("Usage: track-state <command> <track-dir> [args...]")
    print("       track-state help [<command>]")
    print()

    for group_name, cmds in _COMMAND_GROUPS:
        print(f"  {group_name}:")
        for c in cmds:
            usage, desc = COMMAND_HELP[c]
            first_line = usage.split("\n")[0]
            print(f"    {first_line:<62s} {desc}")
        print()


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        target = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "help" else None
        cmd_help(target)
        sys.exit(0)

    # Commands that take no track-dir positional (their [optional] positional is
    # a query/shortname, not a path). They may legally run with len(argv) == 2.
    _NO_TRACK_DIR_COMMANDS = {"resolve-track", "check", "setup", "new-track-resume"}

    cmd = sys.argv[1]
    if len(sys.argv) < 3 and cmd not in _NO_TRACK_DIR_COMMANDS:
        print("Usage: track-state <command> <track-dir> [args...]", file=sys.stderr)
        print("       track-state help [<command>]", file=sys.stderr)
        sys.exit(1)

    track_dir = sys.argv[2] if len(sys.argv) >= 3 else None
    args = sys.argv[3:]
    pos = positional(args)

    # Universal short-id resolution: accept a bare track_id / shortname wherever
    # a <track-dir> positional is an existing track to locate. Skipped for the
    # query commands (resolve their own query / take no positional), the raw-path
    # commands (destination/diagnostic, not a lookup — see
    # ``_TD_NO_RESOLUTION_COMMANDS``), and ``derive-name`` (its positional is a
    # shortname, not a track). No-op for a real path (single is_dir() fast path).
    if track_dir is not None and cmd not in (
        _NO_TRACK_DIR_COMMANDS | _TD_NO_RESOLUTION_COMMANDS | {"derive-name"}
    ):
        track_dir = _resolve_track_dir_or_halt(track_dir, cmd)

    _INDEX_COMMANDS = {"lock", "complete", "fail", "skip", "block", "defer"}

    try:
        if cmd in _INDEX_COMMANDS:
            p, t, s = resolve_indices(pos, args)
            if p is None or t is None:
                out(dict(
                    error="Missing phase/task index. Provide positional args or named flags.",
                    hint="Usage: track-state %s <dir> <phase> <task> [<subtask>] "
                         "OR --phase N --task N [--subtask N]" % cmd))
                sys.exit(1)

            p, t, s = _fix_zero_based_input(track_dir, p, t, s)

            if cmd == "lock":
                cmd_lock(track_dir, p, t, s)
            elif cmd == "complete":
                cov = flag(args, "--coverage")
                dev = flag(args, "--deviations")
                try:
                    cov_val = int(cov) if cov else None
                    dev_val = int(dev) if dev else None
                except ValueError:
                    out(dict(error=f"--coverage and --deviations require integers, got: {cov!r} {dev!r}"))
                    sys.exit(1)
                cmd_complete(track_dir, p, t, s,
                             flag(args, "--sha"),
                             coverage=cov_val,
                             deviations=dev_val)
            elif cmd == "fail":
                cmd_fail(track_dir, p, t, s,
                         flag(args, "--summary") or "")
            elif cmd == "skip":
                cmd_skip(track_dir, p, t, s,
                         flag(args, "--reason") or "")
            elif cmd == "block":
                cmd_block(track_dir, p, t, s,
                          flag(args, "--reason") or "")
            elif cmd == "defer":
                cmd_defer(track_dir, p, t, s,
                          flag(args, "--reason") or "")

        elif cmd == "next":
            cmd_next(track_dir, compact="--full" not in args)
        elif cmd == "dispatch-next":
            cmd_dispatch_next(track_dir, compact="--full" not in args)
        elif cmd == "recover":
            cmd_recover(track_dir, compact="--full" not in args)
        elif cmd == "reset":
            scope = flag(args, "--scope") or "task"
            cmd_reset(track_dir, scope,
                      flag(args, "--phase"),
                      flag(args, "--task"))
        elif cmd == "deferred-report":
            cmd_deferred_report(track_dir)
        elif cmd == "sync-plan":
            cmd_sync_plan(track_dir)
        elif cmd == "registry-update":
            if len(pos) < 1:
                out(dict(error="Missing tracks-md-path argument"))
                sys.exit(1)
            cmd_registry_update(track_dir, pos[0])
        elif cmd == "registry-add":
            # tracks-md-path is optional — registry-add auto-locates the
            # registry (walk-up, then alongside the track) when omitted, so the
            # new-track skill never hand-computes the path.
            cmd_registry_add(track_dir, pos[0] if pos else None)
        elif cmd == "start":
            cmd_start(track_dir)
        elif cmd == "set-mode":
            cmd_set_mode(track_dir, flag(args, "--mode"))
        elif cmd == "indices":
            cmd_indices(track_dir)
        elif cmd == "validate":
            cmd_validate(track_dir, fix="--fix" in args)
        elif cmd == "phase-done":
            cmd_phase_done(track_dir, pos[0])
        elif cmd == "shas":
            cmd_shas(track_dir)
        elif cmd == "post-loop-status":
            cmd_post_loop_status(track_dir)
        elif cmd == "quality-snapshot":
            cmd_quality_snapshot(track_dir)
        elif cmd == "spec-integrity":
            cmd_spec_integrity(track_dir)
        elif cmd == "add-checkpoint":
            if len(pos) < 2:
                out(dict(error="Missing phase or sha argument"))
                sys.exit(1)
            cmd_add_checkpoint(track_dir, pos[0], pos[1])
        elif cmd == "finalize":
            cmd_finalize(track_dir)
        elif cmd == "archive":
            cmd_archive(track_dir, force="--force" in args)
        elif cmd == "gc":
            cmd_gc(track_dir)
        elif cmd == "checklist-verify":
            cmd_checklist_verify(track_dir)
        elif cmd == "process-result":
            cmd_process_result(track_dir)
        elif cmd == "write-result":
            cmd_write_result(track_dir)
        elif cmd == "dispatch-prepare":
            cmd_dispatch_prepare(track_dir, compact="--full" not in args)
        elif cmd == "dispatch-finalize":
            cmd_dispatch_finalize(track_dir, compact="--full" not in args)
        elif cmd == "step":
            cmd_step(track_dir, compact="--full" not in args)
        elif cmd == "post-loop-step":
            cmd_post_loop_step(track_dir, compact="--full" not in args)
        elif cmd == "post-loop-review":
            cmd_post_loop_review(track_dir, flag(args, "--status"),
                                 critical=flag(args, "--critical"),
                                 high=flag(args, "--high"))
        elif cmd == "phase-verdict":
            cmd_phase_verdict(
                track_dir,
                flag(args, "--ac-verdict"),
                flag(args, "--ac-gate"),
                flag(args, "--ac-n-ungrounded"),
                flag(args, "--l1-status"),
                flag(args, "--l1-command"))
        elif cmd == "phase-checkpoint-review":
            cmd_phase_checkpoint_review(
                track_dir, flag(args, "--status"),
                flag(args, "--sha"), flag(args, "--reason"))
        elif cmd == "skip-analyst-verdict":
            cmd_skip_analyst_verdict(
                track_dir, flag(args, "--recommendation"),
                flag(args, "--reasoning"), flag(args, "--impact"),
                flag(args, "--can-skip"))
        elif cmd == "skip-refute-review":
            cmd_skip_refute_review(
                track_dir, flag(args, "--status"), flag(args, "--reasoning"))
        elif cmd == "record-summary":
            cmd_record_summary(track_dir)
        elif cmd == "dispatch-wave":
            cmd_dispatch_wave(track_dir, compact="--full" not in args)
        elif cmd == "wave-status":
            cmd_wave_status(track_dir, compact="--full" not in args)
        elif cmd == "wave-finalize":
            # wave-finalize integrates one member at a time, so it takes explicit
            # --phase/--task indices (no singleton cursor under a wave). Resolve
            # through the same channel as the index commands so both positional
            # and named-flag forms work.
            p, t, _ = resolve_indices(pos, args)
            if p is None or t is None:
                out(dict(error="wave-finalize requires --phase and --task"))
                sys.exit(1)
            cmd_wave_finalize(track_dir, p, t, compact="--full" not in args)
        elif cmd == "wave-abort":
            cmd_wave_abort(track_dir, compact="--full" not in args)
        elif cmd == "wave-step":
            cmd_wave_step(track_dir, compact="--full" not in args)
        elif cmd == "init-from-plan":
            cmd_init_from_plan(track_dir,
                               flag(args, "--track-id") or "track",
                               flag(args, "--type") or "feature",
                               flag(args, "--description") or "",
                               flag(args, "--execution-mode"),
                               check="--check" in args,
                               force="--force" in args)
        elif cmd == "get-handoff":
            cmd_get_handoff(track_dir, pos[0], pos[1],
                           flag(args, "--subtask"))
        elif cmd == "sync-handoff":
            cmd_sync_handoff(track_dir)
        elif cmd == "append-handoff":
            content = flag(args, "--content")
            if content is None:
                # No --content flag → read JSON from stdin (same quote-safe
                # channel write-result uses; lets agents pipe a heredoc instead
                # of hand-quoting inline --content '<json>').
                content = sys.stdin.read()
            cmd_append_handoff(track_dir, pos[0], pos[1],
                              flag(args, "--type") or "explore",
                              content,
                              flag(args, "--subtask"))
        elif cmd == "harvest-candidates":
            cmd_harvest_candidates(track_dir)
        elif cmd == "preflight":
            cmd_preflight(track_dir)
        elif cmd == "derive-name":
            cmd_derive_name(sys.argv[2])  # shortname — the one positional that isn't a track-dir
        elif cmd == "new-track-init":
            cmd_new_track_init(track_dir,
                               flag(args, "--track-id") or "track",
                               flag(args, "--description") or "",
                               flag(args, "--type") or "feature")
        elif cmd == "new-track-step":
            if not pos:
                out(dict(error="Missing resume step key",
                         hint="one of: spec_planned|reviewed|state_created|registry_updated"))
                sys.exit(1)
            cmd_new_track_step(track_dir, pos[0])
        elif cmd == "new-track-set-mode":
            cmd_new_track_set_mode(track_dir, flag(args, "--mode"))
        elif cmd == "new-track-resume":
            cmd_new_track_resume()
        elif cmd == "new-track-finalize":
            cmd_new_track_finalize(track_dir)
        elif cmd in ("resolve-track", "check", "setup"):
            # Re-derive from argv[2:] (not the shared track_dir/args split):
            # `check --registry X` would otherwise eat the flag name into the
            # track_dir slot. All three share the query/flag shape; ``setup`` is
            # the pre-rename alias routed to the same ``cmd_check``.
            raw = sys.argv[2:]
            query = None if (not raw or raw[0].startswith("--")) else raw[0]
            _CHECK_FNS = {"resolve-track": cmd_resolve_track,
                          "check": cmd_check, "setup": cmd_check}
            _CHECK_FNS[cmd](query=query, registry_path=flag(raw, "--registry"))
        else:
            print(f"Unknown command: {cmd}", file=sys.stderr)
            sys.exit(1)
    except SystemExit:
        raise
    except IndexError as e:
        msg = str(e)
        if msg == "list index out of range":
            out(dict(
                error="Internal index error — this usually means a missing or "
                      "incorrect positional argument.",
                hint="Use --phase N --task N [--subtask N] flags or check "
                     "'track-state indices <track-dir>' for valid indices"))
        else:
            out(dict(error=msg,
                     hint="Run 'track-state validate --fix' to correct state"))
        sys.exit(1)
    except Exception as e:
        out(dict(error=f"{type(e).__name__}: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
