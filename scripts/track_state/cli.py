"""CLI entry point: argument parsing and command routing."""
import json
import sys
from pathlib import Path

from .core import load
from .constants import EXECUTION_MODES
from .helpers import out, flag
from .mutations import cmd_lock, cmd_fail, cmd_skip, cmd_block, cmd_defer
from .cmd_complete import cmd_complete
from .dispatch import cmd_next, cmd_dispatch_next, cmd_dispatch_prepare, cmd_dispatch_finalize, cmd_recover
from .result import cmd_process_result, cmd_write_result
from .validate import cmd_validate
from .quality import cmd_init, cmd_init_from_plan, cmd_start, cmd_set_mode, cmd_finalize, cmd_archive, cmd_gc, cmd_checklist_verify
from .misc import (
    cmd_reset, cmd_indices, cmd_shas, cmd_add_checkpoint,
    cmd_deferred_report, cmd_phase_done, cmd_registry_update,
    cmd_record_summary,
)
from .handoff import cmd_get_handoff, cmd_sync_handoff, cmd_append_handoff
from .sync import cmd_sync_plan


_BOOL_FLAGS = {"--compact", "--fix", "--check", "--force"}


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
    "init": ("init <track-dir> --plan-structure <json> --track-id <id>\n"
             "              --type <feature|bugfix|chore|docs> --description <text>\n"
             f"              [--execution-mode <{_EXEC_MODE_CHOICES}>]",
             "Create track-state.json and index.md from plan structure"),
    "init-from-plan": ("init-from-plan <track-dir> --track-id <id>\n"
                       "                  --type <feature|bugfix|chore|docs> --description <text>\n"
                       f"                  [--execution-mode <{_EXEC_MODE_CHOICES}>] [--check]",
                       "Create track-state.json by parsing plan.md (validates plan syntax)"),
    "start": ("start <track-dir>",
              "Transition track from 'new' to 'in_progress'"),
    "set-mode": (f"set-mode <track-dir> --mode <{_EXEC_MODE_CHOICES}>",
                 "Switch execution_mode on an existing track (no re-init)"),
    "next": ("next <track-dir> [--compact]",
             "Find the next task to execute (JSON or compact format)"),
    "dispatch-next": ("dispatch-next <track-dir>",
                      "One-call dispatch: next + parent-complete + tag routing"),
    "recover": ("recover <track-dir> [--compact]",
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
                       "                  --content '<json>' [--subtask <n>]",
                       "Append notes to a task's handoff file"),
    "registry-update": ("registry-update <track-dir> <tracks-md-path>",
                        "Update track entry in Tracks Registry (tracks.md)"),
    "write-result": ("write-result <track-dir> [--data '<json>']",
                     "Write result.json from --data or stdin (SUCCESS|FAILURE)"),
    "process-result": ("process-result <track-dir>",
                       "Read result.json, update state, sync plan, write git notes, enforce gates"),
    "dispatch-prepare": ("dispatch-prepare <track-dir>",
                         "Composite: next + lock + sync-plan + route (reduces round trips)"),
    "dispatch-finalize": ("dispatch-finalize <track-dir> [--override k=v,k2=v2]",
                          "Composite: process-result + conductor commit + sync-plan. --override patches empty result fields"),
    "record-summary": ("record-summary <track-dir>",
                       "Record compact task summary (stdin JSON) for post-compaction recovery"),
    "validate": ("validate <track-dir> [--fix]",
                 "Validate state; always reports auto-fix analysis, --fix persists repairs"),
    "gc": ("gc <track-dir>",
           "Garbage collection: clean orphaned artifacts, detect stale state"),
    "shas": ("shas <track-dir>",
             "List all commit SHAs from completed tasks"),
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
}

_COMMAND_GROUPS = [
    ("Lifecycle", ["init", "init-from-plan", "start", "set-mode", "finalize", "archive"]),
    ("Navigation", ["next", "dispatch-next", "recover", "indices"]),
    ("State Mutations", ["lock", "complete", "fail", "skip", "defer", "block", "reset"]),
    ("Sync & Registry", ["sync-plan", "sync-handoff", "registry-update"]),
    ("Handoff", ["get-handoff", "append-handoff"]),
    ("Result Processing", ["write-result", "process-result"]),
    ("Dispatch Composites", ["dispatch-prepare", "dispatch-finalize", "record-summary"]),
    ("Diagnostics", ["validate", "gc", "shas", "checklist-verify", "deferred-report",
                     "phase-done", "add-checkpoint"]),
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

    if len(sys.argv) < 3:
        print("Usage: track-state <command> <track-dir> [args...]", file=sys.stderr)
        print("       track-state help [<command>]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    track_dir = sys.argv[2]
    args = sys.argv[3:]
    pos = positional(args)

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
            cmd_next(track_dir, compact="--compact" in args)
        elif cmd == "dispatch-next":
            cmd_dispatch_next(track_dir)
        elif cmd == "recover":
            cmd_recover(track_dir, compact="--compact" in args)
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
            cmd_dispatch_prepare(track_dir)
        elif cmd == "dispatch-finalize":
            cmd_dispatch_finalize(track_dir)
        elif cmd == "record-summary":
            cmd_record_summary(track_dir)
        elif cmd == "init":
            cmd_init(track_dir,
                     flag(args, "--plan-structure") or "{}",
                     flag(args, "--track-id") or "track",
                     flag(args, "--type") or "feature",
                     flag(args, "--description") or "",
                     flag(args, "--execution-mode"))
        elif cmd == "init-from-plan":
            cmd_init_from_plan(track_dir,
                               flag(args, "--track-id") or "track",
                               flag(args, "--type") or "feature",
                               flag(args, "--description") or "",
                               flag(args, "--execution-mode"),
                               check="--check" in args)
        elif cmd == "get-handoff":
            cmd_get_handoff(track_dir, pos[0], pos[1],
                           flag(args, "--subtask"))
        elif cmd == "sync-handoff":
            cmd_sync_handoff(track_dir)
        elif cmd == "append-handoff":
            cmd_append_handoff(track_dir, pos[0], pos[1],
                              flag(args, "--type") or "explore",
                              flag(args, "--content") or "{}",
                              flag(args, "--subtask"))
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
