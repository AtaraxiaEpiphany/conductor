"""Quality scoring and track lifecycle commands."""
import json
import os
import re
import shutil
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import load, save
from .git_ops import docs_synced_for_track, _git_commit
from .helpers import out, now_iso, conductor_dir, _reset_task, _resolve_conductor_root
from .constants import EXECUTION_MODES, RECOVERY_POLICIES
from .handoff import _ensure_handoff_index
from .validate import _parse_plan_structure
from .plan_parse import parse_plan, to_plan_structure
from .task_profiles import derive_task_type, derive_task_tag, strip_dispatch_tags
# Marker filenames/templates single-homed in lib.constants — the gitignore
# tuple below derives from the exact constants the writer modules use.
from lib.constants import (
    RESULT_MARKER, NT_PROGRESS_MARKER, PHASE_CHECKPOINT_MARKER,
    SKIP_ANALYSIS_MARKER, REVIEW_SEEN_MARKER, REVIEW_RESULT_MARKER,
    WAVE_LEDGER_NAME, WAVE_MARKER_NAME, WAVE_DRAIN_MARKER_NAME,
    DISPATCH_LOCK_NAME, BRIEF_PROGRESS_MARKER, FAILURE_ANALYSIS_MARKER,
    PHASE_RECOVERY_MARKER, AMENDMENT_STAGED_MARKER, DISPATCH_MANIFEST_MARKER,
    DISPATCH_INFLIGHT_TMPL, TRIPWIRE_COUNT_TMPL,
    MODIFIED_GUIDANCE_TMPL, AMENDMENT_GUIDANCE_TMPL,
)


def _checklist_status(track_dir):
    """Return verification status by reading track-state.json directly."""
    state = load(track_dir)
    total = 0
    verified = 0
    unverified = []
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            total += 1
            key = f"P{pi}.T{ti} {task['name']}"
            if task["status"] == "completed":
                verified += 1
            else:
                unverified.append(key)
            for si, sub in enumerate(task.get("subtasks", []), 1):
                total += 1
                subkey = f"P{pi}.T{ti}.S{si} {sub['name']}"
                if sub["status"] == "completed":
                    verified += 1
                else:
                    unverified.append(subkey)
    return dict(exists=True, total=total, verified=verified, unverified=unverified)


def cmd_checklist_verify(track_dir):
    """Check feature checklist verification status."""
    status = _checklist_status(track_dir)
    out(status)


# ── Init (Track Creation) ────────────────────────────────────────────


def _validate_plan_structure(plan):
    """Validate PLAN_STRUCTURE input before building state. Returns list of errors."""
    errors = []
    phases = plan.get("phases")
    if not isinstance(phases, list) or len(phases) == 0:
        errors.append("PLAN_STRUCTURE must have at least 1 phase")
        return errors
    for pi, phase in enumerate(phases, 1):
        if not phase.get("name"):
            errors.append(f"Phase {pi}: missing name")
        tasks = phase.get("tasks")
        if not isinstance(tasks, list) or len(tasks) == 0:
            errors.append(f"Phase {pi} '{phase.get('name', '?')}': must have at least 1 task")
            continue
        for ti, task in enumerate(tasks, 1):
            if not task.get("name"):
                errors.append(f"Phase {pi} Task {ti}: missing name")
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if isinstance(sub, dict) and not sub.get("name"):
                    errors.append(f"Phase {pi} Task {ti} Subtask {si}: missing name")
    return errors


# Transient subagent artifacts that must never be swept into conductor commits.
#
# NORMATIVE SOURCE = the _TRANSIENT_MARKERS tuple below (each entry is a
# (gitignore_pattern, concrete_sample) pair; the sample is consumed by
# tests/test_conductor_gitignore.py to prove the pattern actually matches).
# This prose is maintainer rationale only — the drift-gate test asserts every
# tuple entry appears in the written .conductor/.gitignore AND is genuinely
# git-ignored, so a new marker added to the tuple is auto-covered and a hand-edit
# to _CONDUCTOR_GITIGNORE is caught. Durably-committed sidecars (post-loop.json,
# track-findings.md, track-directives.md) are deliberately NOT in the tuple.
#
# Every FILENAME below is imported from lib.constants (the single home shared
# with the writer modules and hooks — see the marker section there); the glob
# families are derived from their path templates via _globify, and each sample
# is rendered from the same template, so a rename or reshape can never leave
# the gitignore behind.
#
# result.json is written by task-executor/explorer and deleted by dispatch-finalize
# each cycle; tracking it only churns git history (committed then re-deleted).
# new-track-progress.json is the new-track resume marker (skills/new-track/SKILL.md
# §0.5) — written before track-state.json exists and deleted once the track commits.
# phase-checkpoint.json is the step-spine checkpoint handshake marker (dispatch.py
# cmd_phase_verdict/cmd_phase_checkpoint_review): carries the fanned verifier verdicts
# between dispatch_batch and the phase-checker synth dispatch, then deleted on both
# terminal outcomes (PASSED also stamps plan.md; FAILED re-fans on re-invoke). Transient.
# skip-analysis.json is the step-spine skip_analyze handshake marker (dispatch.py
# cmd_skip_analyst_verdict/cmd_skip_refute_review): carries skip-analyst's recommendation
# (+reasoning) between dispatch_skip_analyst and the conditional refuter dispatch, then the
# refute STATUS; deleted on both terminal outcomes (skip executes + advances; halt re-analyzes).
# parallel.json + wave-agent.marker are the worktree-wave parallelism runtime
# state (scripts/track_state/wave.py): the sidecar ledger tracks in-flight members
# and the marker short-circuits the SubagentStop hook for wave agents. Both are
# per-run and must never be committed — staging them would churn history and leak
# absolute worktree paths into the repo.
# .wave-drain-processed is the wave-step drain marker (scripts/track_state/wave.py
# cmd_wave_step): records that a drained wave's post-drain decisions (seam-review
# applicability) were made, keyed on (track_id, base_sha). Per-run bookkeeping —
# committing it would leak state across tracks and survive past the wave it marks.
# .dispatch-inflight-*.json is the single-writer guard marker (lib/dispatch_inflight,
# on-dispatch-dedupe.py PreToolUse:Agent hook): stamped by prepare_dispatch and
# cleared on finalize/reap; transient lock state, never staged.
# .tripwire-*.count is the PreToolUse round-trip counter (on-pre-tool-tripwire.py):
# reset on dispatch, bumped every tool round; per-run counter, never committed.
# .modified-guidance-*.md is the failure-analyst retry_modified injection marker
# (dispatch.py _modified_guidance_write / on-subagent-start.py): stamped on a
# retry-with-modified-guidance decision, consumed-on-read and cleared; transient
# plumbing, never staged.
# brief-progress.json is the /conductor:brief resume marker (brief.py): written
# before track-state.json exists and deleted at §5 hand-off; transient. (Was missing
# from the ignore list — brief.py's docstring falsely claimed "gitignored".)
# failure-analysis.json is the failure-analyst handshake marker (dispatch.py
# _FAILURE_ANALYSIS_MARKER): carries the diagnosis between dispatch and the
# retry/replan/decompose verdict; consumed/cleared on resolution. Transient.
# phase-recovery.json is the phase-checkpoint FAILED→recovery marker (dispatch.py
# _PHASE_RECOVERY_MARKER): routes a failed checkpoint through the failure analyst
# + verdict router; cleared when the next terminal verdict resolves it. Transient.
# amendment-staged.json is the spec-amendment staging marker (dispatch.py
# _AMENDMENT_STAGED_MARKER): carries a staged ## Amendment until applied/cleared. Transient.
# .amendment-guidance-*.md is the amendment retry-guidance injection (dispatch.py
# _amendment_guidance_path / on-subagent-start.py): consumed-on-read and cleared,
# mirroring .modified-guidance-*.md. Transient.
# review-result.json is the post-loop code-review findings marker (on-subagent-stop.py):
# carries review findings for the post-loop spine; transient per cycle.
_RESULT_TMP_GLOB = ".result.tmp.*"  # explorer scratch files; globbed below in cleanup


def _globify(tmpl: str) -> str:
    """Render a marker path template as its gitignore glob (every field → *)).

    ``.modified-guidance-{pi}-{ti}{sub}.md`` → ``.modified-guidance-*-*.md``
    (adjacent stars collapsed so ``{ti}{sub}`` doesn't emit ``**``). The glob
    and the writer's filename can't drift — both come from the one template.
    """
    parts = []
    for literal, field, _spec, _conv in string.Formatter().parse(tmpl):
        parts.append(literal)
        if field:
            parts.append("*")
    return re.sub(r"\*{2,}", "*", "".join(parts))


# (gitignore_pattern, concrete_sample) — the sample lets the drift-gate test
# prove glob patterns actually match without a parallel drift-prone dict.
# Filenames: lib.constants (single home). Samples: rendered from the same
# templates as the globs.
_TRANSIENT_MARKERS = (
    (RESULT_MARKER, RESULT_MARKER),
    (_RESULT_TMP_GLOB, ".result.tmp.abc123"),  # explorer-written scratch; quality-owned glob
    (NT_PROGRESS_MARKER, NT_PROGRESS_MARKER),
    (PHASE_CHECKPOINT_MARKER, PHASE_CHECKPOINT_MARKER),
    (SKIP_ANALYSIS_MARKER, SKIP_ANALYSIS_MARKER),
    (REVIEW_SEEN_MARKER, REVIEW_SEEN_MARKER),  # skill-prose-owned (implement §3.6b)
    (WAVE_LEDGER_NAME, WAVE_LEDGER_NAME),
    (WAVE_MARKER_NAME, WAVE_MARKER_NAME),
    (WAVE_DRAIN_MARKER_NAME, WAVE_DRAIN_MARKER_NAME),
    (_globify(DISPATCH_INFLIGHT_TMPL),
     DISPATCH_INFLIGHT_TMPL.format(phase=1, task=1, sub="")),
    (DISPATCH_LOCK_NAME, DISPATCH_LOCK_NAME),
    (_globify(TRIPWIRE_COUNT_TMPL),
     TRIPWIRE_COUNT_TMPL.format(phase=2, task=3, sub="")),
    (_globify(MODIFIED_GUIDANCE_TMPL),
     MODIFIED_GUIDANCE_TMPL.format(pi=1, ti=1, sub="")),
    (BRIEF_PROGRESS_MARKER, BRIEF_PROGRESS_MARKER),
    (FAILURE_ANALYSIS_MARKER, FAILURE_ANALYSIS_MARKER),
    (PHASE_RECOVERY_MARKER, PHASE_RECOVERY_MARKER),
    (AMENDMENT_STAGED_MARKER, AMENDMENT_STAGED_MARKER),
    (_globify(AMENDMENT_GUIDANCE_TMPL),
     AMENDMENT_GUIDANCE_TMPL.format(pi=1, ti=1, sub="")),
    (REVIEW_RESULT_MARKER, REVIEW_RESULT_MARKER),
    (DISPATCH_MANIFEST_MARKER, DISPATCH_MANIFEST_MARKER),
)
_PATTERNS = tuple(pattern for pattern, _sample in _TRANSIENT_MARKERS)
# Derived so the ignore body and the drift-gate test's source of truth cannot
# diverge. Format pinned: header line + newline-joined patterns + trailing newline.
_CONDUCTOR_GITIGNORE = (
    "# Conductor runtime artifacts — transient, never commit.\n"
    + "\n".join(_PATTERNS) + "\n"
)


def _ensure_conductor_gitignore(track_path):
    """Write .conductor/.gitignore (idempotent) so transient subagent artifacts
    are never staged by conductor commits. Self-contained per-track — no project
    -root .gitignore dependency."""
    cond = Path(track_path) / ".conductor"
    cond.mkdir(parents=True, exist_ok=True)
    (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE)


def _mode_error(mode, allow_none=False):
    """Return an error string if ``mode`` is not a valid execution mode, else None.

    With ``allow_none`` (used by init, where None means "leave unset"), a null
    mode is accepted. Without it (used by set-mode), None is rejected.
    """
    if mode is None:
        if allow_none:
            return None
        return f"Missing execution_mode. Must be one of: {', '.join(EXECUTION_MODES)}."
    if mode not in EXECUTION_MODES:
        return (f"Invalid execution_mode {mode!r}. "
                f"Must be one of: {', '.join(EXECUTION_MODES)}.")
    return None


def _recovery_policy_error(policy, allow_none=False):
    """Return an error string if ``policy`` is not a valid recovery policy, else None.

    Mirrors :func:`_mode_error`. ``allow_none`` (used by init, where None means
    "leave unset → new-track default applies later") accepts a null policy;
    without it (used by set-recovery-policy) None is rejected.
    """
    if policy is None:
        if allow_none:
            return None
        return f"Missing recovery_policy. Must be one of: {', '.join(RECOVERY_POLICIES)}."
    if policy not in RECOVERY_POLICIES:
        return (f"Invalid recovery_policy {policy!r}. "
                f"Must be one of: {', '.join(RECOVERY_POLICIES)}.")
    return None


def _init_core(track_dir, plan, track_id, track_type, description, execution_mode=None,
               force=False):
    """Build track-state.json + index.md + handoff.md from a plan structure dict.

    Returns the result dict without printing. Consumed by cmd_init_from_plan
    (parsed from plan.md).

    ``force`` re-bootstraps an existing track (resets all progress to pending).
    Without it, an existing track-state.json is refused — re-running init on a
    live track would otherwise silently reconstruct state from plan.md and wipe
    every task's status/SHA (V7, core-contract.md).
    """
    errors = _validate_plan_structure(plan)
    if errors:
        return dict(ok=False, errors=errors)

    mode_err = _mode_error(execution_mode, allow_none=True)
    if mode_err:
        return dict(ok=False, errors=[mode_err])

    # schemas/track-state.schema.json:11 requires ^[a-z0-9_]+_\d{8}$ (shortname_YYYYMMDD).
    # "track" is the cli.py default when --track-id is omitted (ad-hoc CLI use); the
    # skills always pass a real id via `derive-name`, so enforce the format there.
    # Checked before mkdir so a bad id never creates a directory.
    if track_id != "track" and not re.match(r"^[a-z0-9_]+_\d{8}$", track_id):
        return dict(ok=False, errors=[
            f"track_id {track_id!r} must match shortname_YYYYMMDD "
            f"(e.g. auth_gateway_20260626). Run: track-state derive-name <shortname>"
        ])

    track_path = Path(track_dir)
    # V7 (core-contract.md): never reconstruct/overwrite EXISTING state from plan.md.
    # The mechanical parse is the sanctioned bootstrap ONLY when no state exists.
    # Re-running init on a live track would silently reset every task to pending
    # (data loss); refuse unless --force explicitly re-bootstraps. Checked before
    # mkdir so a refusal never creates a directory either.
    state_path = track_path / "track-state.json"
    if state_path.exists() and not force:
        return dict(ok=False, errors=[
            f"track-state.json already exists at {state_path}. "
            f"Pass --force to re-bootstrap (this resets all task progress to pending)."
        ])

    track_path.mkdir(parents=True, exist_ok=True)

    # Pre-plan → post-plan boundary: any result.json present before state exists
    # is an orphan from a consumer-free window (the §2.2.5 grounding fan-out's
    # parallel explorers share the single-slot mailbox, last-write-wins). State
    # creation implies the result slot is clean — dispatch-prepare would clear
    # it anyway (dispatch.py _clear_stale_result); reaping HERE makes that
    # ordering explicit instead of implicit-in-the-skill-flow. Raw path, not
    # conductor_dir() (which mkdirs — a read must not mint .conductor/).
    (track_path / ".conductor" / RESULT_MARKER).unlink(missing_ok=True)

    # Build track-state.json from the plan structure
    phases = []
    for phase in plan.get("phases", []):
        tasks = []
        for task in phase.get("tasks", []):
            # task_type is a typed mirror of the name's tag, derived once at
            # construction. The name stays authoritative (reconcile/sync key
            # on it); this field is a cache the spine reads instead of
            # re-parsing extract_tags at every dispatch.
            parent_type = derive_task_type(task["name"])
            entry = {
                "name": task["name"],
                "status": "pending",
                "task_type": parent_type,
            }
            if "subtasks" in task:
                entry["subtasks"] = [
                    {
                        "name": st["name"] if isinstance(st, dict) else st,
                        "status": "pending",
                        # Subtasks inherit the parent's tag (contract rule:
                        # never tag subtasks individually), so they reuse the
                        # already-derived parent task_type — a subtask name
                        # carries no tag of its own.
                        "task_type": parent_type,
                    }
                    for st in task["subtasks"]
                ]
            tasks.append(entry)
        phases.append({"name": phase["name"], "status": "pending", "tasks": tasks})

    state = {
        "track_id": track_id,
        "type": track_type,
        "status": "new",
        "description": description,
        "current_phase_index": 1,
        "current_task_index": 1,
        # Third-axis (workflow-shapes): the declared topology this track runs.
        # v1 always writes "default" (the planner→executor→checker loop). A
        # future init-from-plan may infer a shape from track-type; the dispatch
        # spine reads this via workflow_shapes.resolve_shape (absent/unknown →
        # "default", fail-open). Advisory load-bearing: a shape_violation is
        # surfaced when a dispatch agent is off-topology (no-silent-caps).
        "workflow_shape": "default",
        # Recovery policy for the failed-task path (decoupled from
        # execution_mode). New tracks default to ``auto``: route a failed+
        # exhausted task straight to the skip-analyst handshake instead of
        # surfacing a Retry/Skip/Block ``ask``. EXISTING tracks (re-init without
        # force never happens; init only writes a fresh state) read absent as
        # ``ask`` via ``state.get`` so they stay byte-identical; ``--force``
        # re-init intentionally takes the new default. Mutated after init via
        # ``set-recovery-policy``.
        "recovery_policy": "auto",
        "updated_at": now_iso(),
        "phases": phases,
    }
    if execution_mode:
        state["execution_mode"] = execution_mode

    save(str(track_path), state)

    # Create index.md from template
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    template_path = Path(plugin_root) / "templates" / "track-index.md" if plugin_root else None
    index_content = None
    if template_path and template_path.exists():
        index_content = template_path.read_text().replace("{TRACK_ID}", track_id)

    if index_content:
        with open(track_path / "index.md", "w") as f:
            f.write(index_content)
    else:
        # Fallback: minimal index
        with open(track_path / "index.md", "w") as f:
            f.write(f"# Track {track_id} Context\n\n")
            f.write("> Paths are relative to this track directory.\n\n")
            f.write("## Track Files\n")
            f.write("- [Specification](./spec.md)\n")
            f.write("- [Implementation Plan](./plan.md)\n")
            f.write("- [Track State](./track-state.json)\n")
            f.write("- [Handoff Index](./handoff.md) (task handoff logs)\n")

    # Create initial handoff.md
    _ensure_handoff_index(str(track_path), state)

    # Ensure .conductor/.gitignore so transient subagent artifacts (result.json,
    # .result.tmp.*) aren't swept into conductor commits.
    _ensure_conductor_gitignore(track_path)

    # Cross-validate plan.md vs track-state.json for task/subtask count mismatches
    warnings = []
    plan_path = track_path / "plan.md"
    if plan_path.exists():
        try:
            plan_struct = _parse_plan_structure(plan_path)
            for pi, state_phase in enumerate(phases, 1):
                plan_phase = plan_struct.get(pi)
                if plan_phase is None:
                    warnings.append(f"Phase {pi}: heading missing in plan.md")
                    continue
                state_tasks = state_phase.get("tasks", [])
                plan_tasks = plan_phase["tasks"]
                if len(plan_tasks) != len(state_tasks):
                    warnings.append(
                        f"Phase {pi}: plan.md has {len(plan_tasks)} tasks, "
                        f"state has {len(state_tasks)}")
                for ti in range(min(len(state_tasks), len(plan_tasks))):
                    state_subs = len(state_tasks[ti].get("subtasks", []))
                    plan_subs = len(plan_tasks[ti].get("subtasks", []))
                    if plan_subs != state_subs:
                        warnings.append(
                            f"P{pi}.T{ti + 1}: plan.md has {plan_subs} subtasks, "
                            f"state has {state_subs}")
        except Exception:
            pass

    task_count = sum(len(p.get("tasks", [])) for p in phases)
    result = dict(ok=True, track_id=track_id, phases=len(phases), tasks=task_count)
    if warnings:
        result["warnings"] = warnings
    return result


def _tag_signal_samples(structure):
    """Per-task declared-vs-signals label SAMPLES — the full instrument, not
    just the disagreements.

    Every top-level task contributes ``{"task", "declared", "suggested",
    "name"}`` (tags lowercased, ``"untagged"`` for none) — agreements
    INCLUDED, because the cross-track rates (disagreement per tag,
    false-untagged rate) need the denominator. This is the labeling
    telemetry Finding-1 method 5 persists at init;
    :func:`_tag_signal_advisories` derives its stdout advisory list from
    these samples (single home). Fail-open: any registry error inside the
    matcher yields no sample for that task (init must never block on the
    lint).
    """
    samples = []
    for pi, phase in enumerate(structure.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            name = task.get("name", "")
            # Original-case declared tag for display; lowercased for compare.
            from .helpers import extract_tags  # lazy: cycle-safe
            declared_tags = extract_tags(name)
            declared = declared_tags[0].lower() if declared_tags else "untagged"
            try:
                suggested = derive_task_tag(strip_dispatch_tags(name))
            except Exception:
                continue
            samples.append(dict(
                task=f"P{pi}.T{ti}",
                declared=declared,
                suggested=suggested.lower() if suggested else "untagged",
                # Display forms keep the registry's original case.
                declared_display=(declared_tags[0] if declared_tags
                                  else "untagged"),
                suggested_display=suggested or "untagged",
                name=strip_dispatch_tags(name),
            ))
    return samples


def _tag_signal_advisories(structure):
    """Declared-vs-signals tag advisories — the R1 lint telemetry.

    Tags are planner-authored content (decision: task-type ownership); the
    keyword matcher is advisory only. For each top-level task, the DECLARED
    leading tag is compared against what the conservative matcher
    (:func:`task_profiles.derive_task_tag` over the tag-stripped name) would
    have suggested. A disagreement is printed, never enforced — every run on
    a real plan is a telemetry sample; silent agreement is the non-event.
    Derived from :func:`_tag_signal_samples` (the single home — the
    persisted instrument); fail-open like the sampler.
    """
    advisories = []
    for s in _tag_signal_samples(structure):
        if s["declared"] == s["suggested"]:
            continue
        shown_declared = (f"[{s['declared_display']}]"
                          if s["declared"] != "untagged" else "untagged")
        shown_suggested = (f"[{s['suggested_display']}]"
                           if s["suggested"] != "untagged" else "untagged")
        advisories.append(
            f"{s['task']}: declared {shown_declared}, signals suggest "
            f"{shown_suggested} — planner judgment wins; double-check "
            f"the label")
    return advisories


def _persist_label_telemetry(track_dir, track_id, structure):
    """Write the per-track labeling telemetry store (Finding-1 method 5).

    ``<track_dir>/.conductor/label-telemetry.json``: every top-level task's
    declared-vs-signals sample (agreements INCLUDED — the cross-track rates
    need the denominator). The stdout advisories remain the run's signal;
    this store is the durable instrument a probe or review reads after the
    fact (disagreement rate per tag, false-untagged rate, MISROUTE feed).
    Plain committed JSON — small, per-track, overwritten on re-init (the
    plan's labels at init time are the sample of record). Fail-open: a write
    failure is advisory, never init-blocking.
    """
    try:
        cdir = conductor_dir(track_dir)
        samples = [
            {k: s[k] for k in ("task", "declared", "suggested", "name")}
            for s in _tag_signal_samples(structure)
        ]
        payload = {
            "track_id": track_id,
            "generated_at": now_iso(),
            "n_tasks": len(samples),
            "samples": samples,
        }
        (cdir / "label-telemetry.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError:
        pass  # telemetry must never block init


def cmd_init_from_plan(track_dir, track_id, track_type, description,
                       execution_mode=None, check=False, force=False):
    """Create track-state.json by parsing <track-dir>/plan.md mechanically.

    Validates plan.md syntax first — errors block initialization so a malformed
    plan never produces state. This replaces the error-prone step of having the
    LLM transcribe plan.md into a PLAN_STRUCTURE JSON: every task and subtask is
    extracted deterministically.

    With --check, validate and print the derived structure without writing.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        out(dict(ok=False, errors=[f"plan.md not found at {plan_path}"]))
        return

    parsed = parse_plan(plan_path)
    plan_warnings = list(parsed["warnings"])

    if parsed["errors"]:
        result = dict(ok=False, errors=parsed["errors"], source=str(plan_path))
        if plan_warnings:
            result["warnings"] = plan_warnings
        out(result)
        return

    structure = to_plan_structure(parsed)
    phase_count = len(structure["phases"])
    task_count = sum(len(p["tasks"]) for p in structure["phases"])
    tag_advisories = _tag_signal_advisories(structure)

    if check:
        result = dict(ok=True, check=True, source=str(plan_path),
                      phases=phase_count, tasks=task_count,
                      structure=structure)
        if plan_warnings:
            result["warnings"] = plan_warnings
        if tag_advisories:
            result["tag_advisories"] = tag_advisories
        out(result)
        return

    result = _init_core(track_dir, structure, track_id, track_type,
                        description, execution_mode, force=force)
    if result.get("ok"):
        # The durable labeling-telemetry sample (agreements included) —
        # stdout keeps the disagreement advisories below.
        _persist_label_telemetry(track_dir, track_id, structure)
    # Structure was derived from plan.md itself, so count cross-checks always
    # pass; the only advisory notes are plan-syntax warnings from parse_plan.
    if plan_warnings and result.get("ok"):
        result["plan_warnings"] = plan_warnings
    if tag_advisories and result.get("ok"):
        result["tag_advisories"] = tag_advisories
    out(result)


# ── Handoff Commands ─────────────────────────────────────────────────────


def cmd_start(track_dir):
    """Transition a track from 'new' to 'in_progress' and own its commit.

    Fully idempotent end-to-end: the bookkeeping commit lives INSIDE the
    ``status == "new"`` branch, so a re-invocation (compaction re-entry, a
    re-run of the step skill) is a true no-op — no second "start" commit. The
    orchestrator never constructs this commit itself (it used to run a prose
    ``git commit`` after ``track-state start`` that was unguarded and produced
    duplicate start commits on re-entry).

    ``_git_commit`` stages only conductor-managed files relative to ``track_dir``
    and commits only if something is staged, so this is a no-op if the state
    mutation produced no diff.
    """
    state = load(track_dir)
    if state.get("status") != "new":
        out(dict(ok=True, status=state.get("status"), message="already started"))
        return

    state["status"] = "in_progress"
    state["updated_at"] = now_iso()
    save(track_dir, state)
    track_id = state.get("track_id") or ""
    msg = f"chore(conductor): Start track '{track_id}'" if track_id else "chore(conductor): Start track"
    _git_commit(track_dir, msg)
    out(dict(ok=True, status="in_progress", committed=True))


def cmd_set_mode(track_dir, mode):
    """Set ``execution_mode`` on an existing track without re-initializing state.

    Lets an in-progress track switch between pausing at phase checkpoints
    (interactive) and auto-proceeding through all phases (continuous).
    """
    mode_err = _mode_error(mode, allow_none=False)
    if mode_err:
        out(dict(ok=False, error=mode_err))
        return

    state = load(track_dir)
    previous = state.get("execution_mode", "interactive")
    state["execution_mode"] = mode
    state["updated_at"] = now_iso()
    save(track_dir, state)
    out(dict(ok=True, execution_mode=mode, previous=previous))


def cmd_set_recovery_policy(track_dir, policy):
    """Set ``recovery_policy`` on an existing track without re-initializing state.

    Mirrors :func:`cmd_set_mode`: validate against the closed vocab, then
    load/set/save, emitting the previous value so the change is visible
    (no-silent-caps — the failed-task decision sites read this field via
    ``dispatch._auto_route_failure``). Lets an in-progress track flip between
    surfacing a Retry/Skip/Block ``ask`` (``ask``) and auto-routing to the
    skip-analyst handshake (``auto``) independently of ``execution_mode``.
    """
    policy_err = _recovery_policy_error(policy, allow_none=False)
    if policy_err:
        out(dict(ok=False, error=policy_err))
        return

    state = load(track_dir)
    # Absent on legacy tracks reads as ``ask`` (the byte-identical default).
    previous = state.get("recovery_policy", "ask")
    state["recovery_policy"] = policy
    state["updated_at"] = now_iso()
    save(track_dir, state)
    out(dict(ok=True, recovery_policy=policy, previous=previous))


def set_workflow_shape(track_dir, shape):
    """Compute+save half of ``set-workflow-shape`` — returns the result dict, no emit.

    The strict ``validate-against-vocab then mutate`` contract: hard-reject an
    unknown shape (a deliberate *set* must not silently become a no-op, even
    though reads fail open to ``default`` on the *read* path). Extracted so the
    shape-studio server (:mod:`registry_studio.set_workflow_shape`) binds a
    track to a shape in-process through the same gate — one definition of
    "set a track's shape" for the CLI, the server, and the tests.
    """
    # Local import: workflow_shapes is read by the dispatch path and resolves
    # the overlay via the project root; importing here (not at module top)
    # keeps the fail-open boundary tight (a set must never crash over registry
    # resolution — a missing shape vocab rejects cleanly below).
    from .workflow_shapes import SHAPES_VOCAB
    vocab = SHAPES_VOCAB()
    if shape not in vocab:
        return dict(ok=False,
                    error=f"unknown workflow_shape {shape!r}",
                    hint=f"known shapes: {', '.join(vocab)}")
    state = load(track_dir)
    previous = state.get("workflow_shape", "default")
    state["workflow_shape"] = shape
    state["updated_at"] = now_iso()
    save(track_dir, state)
    return dict(ok=True, workflow_shape=shape, previous=previous)


def cmd_set_workflow_shape(track_dir, shape):
    """Set ``workflow_shape`` on an existing track (the topology declaration).

    Unlike ``task_type`` (re-derived from the name), ``workflow_shape`` is a
    *declaration* with no upstream source — so it lives in state and is mutable
    via this command.
    Mirrors :func:`cmd_set_mode`: validate against the resolved shape vocab,
    then load/set/save, emitting the previous value so the change is visible
    (no-silent-caps — dispatch reads this field as its node allowlist).

    Hard-rejects an unknown shape. This is deliberate: ``resolve_shape`` fails
    open to ``default`` on *reads* (a typo must never block dispatch), but a
    deliberate *set* must not silently become a no-op — validate before mutate,
    so the source of truth is never left holding an unrecognized name. The
    compute half is :func:`set_workflow_shape` (shared with the studio server).
    """
    out(set_workflow_shape(track_dir, shape))


# Statuses that are acceptable end-states for a COMPLETED track (finalize).
# failed/blocked are intentionally excluded — they flip the track to failed/blocked
# via the earlier branches. pending/in_progress mean work remains and finalize
# must refuse false completion rather than declaring the track done. `cancelled`
# IS acceptable: a fully-cancelled track is a legitimate (if void) end-state.
_FINALIZE_OK_STATUSES = ("completed", "skipped", "deferred", "cancelled")


def _finalize_track(track_dir):
    """Compute+save half of ``finalize`` — returns the result dict, no emit.

    Extracted so ``cmd_post_loop_step`` (Rail B-min post-loop spine) can run the
    finalize step inline and route on its outcome (``halt`` on ok:false) in the
    same call. Mirrors ``finalize_dispatch`` / ``cmd_dispatch_finalize``.
    """
    state = load(track_dir)
    state["current_phase_index"] = 0
    state["current_task_index"] = 0
    state.pop("current_subtask_index", None)

    statuses = []
    for phase in state["phases"]:
        for task in phase["tasks"]:
            statuses.append(task["status"])
            for sub in task.get("subtasks", []):
                statuses.append(sub["status"])

    if "blocked" in statuses:
        state["status"] = "blocked"
    elif "failed" in statuses:
        state["status"] = "failed"
    elif all(s in _FINALIZE_OK_STATUSES for s in statuses):
        state["status"] = "completed"
    else:
        # Non-terminal tasks (pending/in_progress) remain — refuse false completion.
        # Keep the track in_progress (schema-valid, marker '~', validate-clean) and
        # surface the unfinished units so the caller can act. No quality_score: an
        # incomplete track has no honest score, and cmd_archive already refuses
        # unless status is 'completed', so archiving is correctly blocked too.
        incomplete = []
        for pi, phase in enumerate(state.get("phases", []), 1):
            for ti, task in enumerate(phase.get("tasks", []), 1):
                if task.get("status") not in _FINALIZE_OK_STATUSES:
                    incomplete.append(f"P{pi}.T{ti} {task.get('name', '?')}: {task.get('status')}")
                for si, sub in enumerate(task.get("subtasks", []), 1):
                    if sub.get("status") not in _FINALIZE_OK_STATUSES:
                        incomplete.append(f"P{pi}.T{ti}.S{si} {sub.get('name', '?')}: {sub.get('status')}")
        state["status"] = "in_progress"
        state["updated_at"] = now_iso()
        save(track_dir, state)
        return dict(ok=False, status="in_progress",
                    reason=f"{len(incomplete)} task(s) still non-terminal",
                    incomplete=incomplete)

    # Feature checklist verification
    checklist = _checklist_status(track_dir)

    # Quality score calculation
    quality_score = _compute_quality_score(track_dir, state, statuses, checklist)

    state["quality_score"] = quality_score
    state["updated_at"] = now_iso()
    save(track_dir, state)

    result = dict(
        ok=True,
        status=state["status"],
        quality_score=quality_score,
    )
    if checklist["exists"]:
        result["checklist"] = dict(
            verified=checklist["verified"],
            total=checklist["total"],
            unverified=checklist["unverified"],
        )
    return result


def cmd_finalize(track_dir):
    """CLI wrapper for :func:`_finalize_track` — emits the result."""
    out(_finalize_track(track_dir))

def _to_number(v):
    """Coerce a free-form ``coverage_pct`` value to a number for scoring.

    int/float pass through; numeric strings ("85", "85.0") parse; anything else
    (None, "", "n/a", bool) → None so the caller can skip it instead of feeding
    a non-numeric into ``sum()``. bool is excluded because ``isinstance(True,
    int)`` is True in Python yet a boolean coverage value is nonsensical.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_quality_score(track_dir, state, statuses, checklist):
    """Compute a 0-100 quality score for the track.
    Weights: completion 40%, checklist 30%, coverage 20%, retry penalty 10%."""
    total = len(statuses)
    if total == 0:
        return 100

    # Completion score (40%): ratio of completed tasks
    completed = statuses.count("completed")
    completion_ratio = completed / total

    # Checklist score (30%): ratio of verified items
    if checklist["exists"] and checklist["total"] > 0:
        checklist_ratio = checklist["verified"] / checklist["total"]
    else:
        checklist_ratio = 1.0  # No checklist = assume full

    # Coverage score (20%): from task evidence, fallback to git notes. Coerce
    # defensively: ``coverage_pct`` is free-form (not schema-enforced int) and
    # has reached state as a numeric string ("85") via result.json propagation
    # / manual edits — a bare ``sum()`` over a str does int(0) + str →
    # ``TypeError: unsupported operand type(s) for +: 'int' and 'str'``, which
    # crashes ``post-loop-step`` (via _finalize_track). Numeric strings still
    # count (85 == "85"); non-numeric junk and bool are skipped. ``_to_number``
    # is shared with ``misc.cmd_quality_snapshot`` so both coverage_pct
    # consumers agree on what counts.
    coverage_values = []
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            ev = task.get("evidence")
            if ev and ev.get("coverage_pct") is not None:
                v = _to_number(ev["coverage_pct"])
                if v is not None:
                    coverage_values.append(v)
            for sub in task.get("subtasks", []):
                sev = sub.get("evidence")
                if sev and sev.get("coverage_pct") is not None:
                    v = _to_number(sev["coverage_pct"])
                    if v is not None:
                        coverage_values.append(v)
    if coverage_values:
        coverage_ratio = sum(coverage_values) / len(coverage_values) / 100
    else:
        coverage_ratio = 0.8  # Default assumption when no evidence

    # Retry penalty (10%): penalize high retry counts
    total_retries = 0
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            total_retries += task.get("retry_count", 0)
    retry_penalty = min(total_retries * 0.05, 0.3)  # Cap at 30% penalty

    score = (completion_ratio * 40 +
             checklist_ratio * 30 +
             coverage_ratio * 20 +
             (1.0 - retry_penalty) * 10)
    return round(min(score, 100))

def cmd_archive(track_dir, force=False):
    """Transition a completed track to archived status AND relocate its directory.

    Flips ``status`` to ``archived`` and moves ``tracks/<id>`` → ``archive/<id>``
    (sibling of ``tracks/`` at the conductor root), so an archived track leaves
    the active set rather than merely being relabeled. The result envelope
    carries the NEW ``track_dir`` (and ``archived_dir``) — callers must use it
    for any subsequent ``registry-update``/commit, since the old path is gone.

    Refuses unless a doc-sync commit exists for this track — evidence the
    post-loop DOC SYNC phase ran and durable findings reached the wiki corpus.
    ``force`` skips the check (the result then carries a ``warning``).
    """
    track_path = Path(track_dir)
    state = load(track_dir)
    current = state.get("status", "")
    track_id = state.get("track_id", "") or track_path.name

    # Idempotent re-entry: a prior run already archived + relocated this track
    # (e.g. interrupted after the move but before the commit). Don't re-move or
    # error — just report the relocated path so the caller can finish the commit.
    if current == "archived" and "archive" in {p.name for p in track_path.parents}:
        dest = track_path.resolve()
        out(dict(ok=True, status="archived", track_dir=str(dest), archived_dir=str(dest),
                 note="already archived and relocated"))
        return

    if current != "completed":
        out(dict(ok=False, error=f"Cannot archive track with status '{current}'. Only 'completed' tracks can be archived.",
                 hint="Run track-state finalize first."))
        return

    synced = docs_synced_for_track(track_dir)
    if not synced and not force:
        out(dict(ok=False,
                 error=(f"Cannot archive track '{track_id}': no doc-sync commit found "
                        f"(docs(conductor): ...[{track_id}]). The post-loop DOC SYNC phase "
                        f"has not run, so durable findings have not been graduated into the wiki corpus."),
                 hint="Run the post-loop DOC SYNC phase (templates/post-loop.md §6.0), or pass --force to archive without it."))
        return

    # Resolve archive/<id> at the conductor root (sibling of tracks/). Fall back
    # for a non-standard layout (no tracks.md ancestor): if the track sits in a
    # dir literally named `tracks`, archive beside it; otherwise archive in place.
    root = _resolve_conductor_root(track_dir)
    if root is not None:
        archive_root = root / "archive"
    elif track_path.parent.name == "tracks":
        archive_root = track_path.parent.parent / "archive"
    else:
        archive_root = track_path.parent / "archive"
    dest = archive_root / track_id

    if dest.exists():
        out(dict(ok=False,
                 error=(f"Cannot archive track '{track_id}': destination already exists "
                        f"('{dest}'). Refusing to overwrite an existing archive entry."),
                 hint="Inspect the destination; rename or remove it, then re-run archive."))
        return

    # Save archived state in place FIRST so track-state.json travels with the move.
    state["status"] = "archived"
    state["archived_at"] = now_iso()
    state["updated_at"] = now_iso()
    save(track_dir, state)

    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(track_path), str(dest))

    result = dict(ok=True, status="archived", track_dir=str(dest), archived_dir=str(dest))
    if not synced:
        result["warning"] = ("Archived without a doc-sync commit (--force); "
                             "durable findings may not be synced to the wiki corpus.")
    out(result)

def cmd_gc(track_dir):
    """Garbage collection: clean orphaned artifacts and detect stale state."""
    track_path = Path(track_dir)
    cond_dir = track_path / ".conductor"
    fixes = []

    # Clean orphaned temp files from interrupted save() / write-result operations
    for pattern in [".track-state.json.tmp*", _RESULT_TMP_GLOB]:
        for tmp_file in track_path.glob(pattern):
            try:
                tmp_file.unlink()
                fixes.append(f"Removed orphaned temp file: {tmp_file.name}")
            except OSError:
                pass
    for tmp_file in cond_dir.glob(_RESULT_TMP_GLOB):
        try:
            tmp_file.unlink()
            fixes.append(f"Removed orphaned temp file: {tmp_file.name}")
        except OSError:
            pass

    # Load state once for all checks below
    try:
        state = load(track_dir)
    except (FileNotFoundError, json.JSONDecodeError):
        state = None

    # Clean orphaned result.json files (left from crashed sessions)
    # Only remove if no task is currently in_progress (i.e., no active processing)
    result_file = cond_dir / "result.json"
    if result_file.exists():
        has_active = False
        if state:
            for phase in state.get("phases", []):
                for task in phase.get("tasks", []):
                    if task.get("status") == "in_progress":
                        has_active = True
                        break
                    for sub in task.get("subtasks", []):
                        if sub.get("status") == "in_progress":
                            has_active = True
                            break
                if has_active:
                    break
        if not has_active:
            result_file.unlink()
            fixes.append("Removed orphaned .conductor/result.json")
        else:
            fixes.append("Skipped .conductor/result.json (active task may be processing it)")

    # Detect stale in_progress tasks (older than 24h)
    if state is None:
        out(dict(fixes=fixes, stale_count=0, age_hours=0))
        return
    now = datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(state.get("updated_at", now.isoformat()))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        updated = now
    age_hours = (now - updated).total_seconds() / 3600

    # Count stale tasks for reporting
    stale_tasks = []
    if age_hours > 24:
        for pi, phase in enumerate(state.get("phases", []), 1):
            for ti, task in enumerate(phase.get("tasks", []), 1):
                if task.get("status") == "in_progress":
                    stale_tasks.append(f"P{pi}.T{ti}: {task.get('name', '?')}")
                for si, sub in enumerate(task.get("subtasks", []), 1):
                    if sub.get("status") == "in_progress":
                        stale_tasks.append(f"P{pi}.T{ti}.S{si}: {sub.get('name', '?')}")
        if stale_tasks:
            fixes.append(f"Stale in_progress tasks detected ({age_hours:.0f}h old): {'; '.join(stale_tasks)}")

    out(dict(fixes=fixes, stale_count=len(stale_tasks), age_hours=round(age_hours, 1)))
