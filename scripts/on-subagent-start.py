#!/usr/bin/env python3
"""SubagentStart hook: inject the safety floor + result-format reminder.

Reads hook input from stdin, outputs JSON with additionalContext. The hook
matcher is matcherless (fires for every subagent); the merged agent-roster
registry (``track_state.agent_roster``) gates who gets what. For every ROSTERED
agent the output is: the universal safety floor
(``runtime/subagent-firewall.md``), then the agent's result-format reminder.
Unrostered agent types fast-no-op with no context — dispatchable (the harness
resolves the name), just no conductor scaffold (the pre-registry unknown-name
behavior, now the fail-open floor for everyone).

This hook is also the SPAWN-TIME home of the dispatch inflight marker
(:func:`_stamp_inflight` → ``lib.dispatch_inflight.stamp``): the marker means
"an agent has demonstrably started", which is the semantics the PreToolUse
dedupe guard needs (a prepare-time stamp made the guard deny the first spawn
itself — the 2026-09-01 dispatch-deadlock incident; see
``lib/dispatch_inflight`` for the full record).

For retry-context agents (task-executor), a third piece is appended when the
locked task has a prior failed attempt: the most recent ``### Attempt ❌`` record
from its handoff. This is the deterministic, can't-be-skipped counterpart to the
agent's own Layer 3.R load — even if the orchestrator under-reports retry status,
the prior failure reason / suggested next step reaches the retry agent here.
"""

import functools
import json
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.constants import TRIPWIRE_COUNT_TMPL  # single home (on-pre-tool-tripwire + gitignore derive here)
from lib.locked_task import resolve as resolve_locked_task
from lib import dispatch_lifecycle as lifecycle


# The universal safety floor injected ahead of every agent's reminder. Single
# source of truth for the cross-agent safety baseline; curated to hold only what
# every subagent must respect (it deliberately omits orchestrator-only rules like
# F1's lock mechanics and the V5/V9 rules that contradict task-executor's workflow).
FLOOR_FILE = Path(__file__).parent.parent / "runtime" / "subagent-firewall.md"


@functools.lru_cache(maxsize=1)
def _load_safety_floor() -> str:
    """Load the universal subagent safety floor.

    Cached for the process: ``subagent-firewall.md`` is a static curatorial doc
    that only changes across plugin upgrades, so it is read once and reused for
    every SubagentStart fire in the session (SubagentStart fires once per
    subagent dispatch — the per-call disk read it replaced was new hot-path I/O).

    Returns '' if the file is missing/unreadable, after warning on stderr so the
    degradation is visible rather than silent (mirrors session-start.py's handling
    of an unreadable core-contract.md). A missing floor must not also drop the
    result-format reminder, so callers fall back to reminder-only on ''.
    """
    try:
        return FLOOR_FILE.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(
            f"[conductor on-subagent-start] WARNING: runtime/subagent-firewall.md "
            f"unreadable ({e}); injecting result-format reminder only.",
            file=sys.stderr,
        )
        return ""


# Result-format reminders live in the agent-roster registry (the third
# registry): ``reminder_for(name)`` composes the "[Conductor] Result format: "
# lead + the row's ``fence``. Agent markdown files already define full role
# behavior; only the delimiter format is reinforced at dispatch —
# filter-subagent-output.py depends on it being present in the subagent's
# emitted output. The same roster owns the retry-context set (``retry``) and the
# registry-vocab injection set (``registry_injection``) — one overlay row gives
# a project agent the whole scaffold with zero plugin edits.


def _roster():
    """The agent-roster registry module, or ``None`` when unimportable.

    Function-level import (the established ``track_state`` pattern — see
    :func:`_resolve_locked_task_type`; hooks run with ``scripts/`` on
    ``sys.path``). ``None`` keeps every caller fail-open (no reminder /
    injection / retry context — the unrostered behavior), never a crashed
    dispatch hook; the registry's own missing/malformed floor is the empty
    roster with a stderr warning (see ``agent_roster``).
    """
    try:
        from track_state import agent_roster
        return agent_roster
    except Exception:
        return None


_REGISTRY_LEAD = (
    "[Conductor Registry] The closed task-type tag set below is resolved at "
    "dispatch (plugin baseline ⊕ project overlay). This is authoritative: "
    "emit/use any tag listed here, and refuse none that are registered — "
    "including project-overlay tags. The closed set is what the registry "
    "resolved to, not a hardcoded list."
)

_RETRY_LEAD = (
    "[Conductor Retry] A prior attempt at this task failed — its handoff record "
    "is below. Do NOT repeat the same approach; heed Failure Reason and "
    "Suggested Next Step. (Full history: track-state get-handoff, Layer 3.R.)"
)

# The header for a failure-analyst "modified retry" directive (B.5). When
# failure-analyst returns ``retry_modified``, the spine writes the analyst's
# modification to a per-task marker (``.conductor/.modified-guidance-<p>-<t>.md``);
# this hook appends it here so the retrying task-executor receives a materially
# different approach, deterministically — even if the orchestrator under-reports
# retry status. Same reason the plain retry nudge lives in the hook, not prose.
_MODIFIED_RETRY_LEAD = (
    "[Conductor Modified Retry] A failure-analyst diagnosed the prior failure "
    "and prescribes a DIFFERENT approach below. Follow it instead of repeating "
    "the prior attempt; the diagnosis explains why the last approach failed."
)


def _modified_guidance_block(track_dir, p, t, s):
    """The failure-analyst modification for this task, or ``None``.

    Reads the per-task modified-guidance marker (written by the spine on a
    ``retry_modified`` verdict). Returns the formatted block and CONSUMES the
    marker (deletes it) so it applies to exactly one retry and doesn't leak into
    a subsequent non-modified dispatch. Best-effort: any I/O error → ``None``
    (advisory; must not break the floor/reminder injection).
    """
    try:
        sub = f"-{s}" if s is not None else ""
        path = Path(track_dir) / ".conductor" / f".modified-guidance-{p}-{t}{sub}.md"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        path.unlink()  # consume-on-read
        if not text:
            return None
        return f"{_MODIFIED_RETRY_LEAD}\n\n{text}"
    except Exception:
        return None


def _amendment_guidance_block(track_dir, p, t, s):
    """The [Conductor Amendment] block for this task, or ``None`` (A3).

    Reads the per-task amendment-guidance marker (written by ``cmd_amend_apply``
    when a failure-analyst ``replan`` verdict amended spec.md). The lead text is
    baked into the file at write time (it lives in dispatch.py), so this just
    reads + CONSUMES (deletes) the marker — it applies to exactly one re-dispatch
    and doesn't leak. Best-effort: any I/O error → ``None`` (advisory; must not
    break the floor/reminder/modified-retry injection). Checked independent of
    the modified-guidance block — a replan retry can carry both an amendment AND
    a modification.
    """
    try:
        sub = f"-{s}" if s is not None else ""
        path = Path(track_dir) / ".conductor" / f".amendment-guidance-{p}-{t}{sub}.md"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        path.unlink()  # consume-on-read
        if not text:
            return None
        return text
    except Exception:
        return None


def _latest_failure_attempt(content):
    """Verbatim text of the most recent ``### Attempt ... ❌`` block, or None.

    A block runs from its ``### Attempt`` heading to the next ``## `` / ``### ``
    heading. Only the LATEST block qualifies, and only if it is a FAILURE (the
    heading carries ❌): a trailing ✅ means the task ultimately completed and
    would not be re-dispatched, so surfacing an older failure would mislead.
    """
    blocks = []
    cur = None
    for line in content.split("\n"):
        if line.startswith("### Attempt "):
            if cur is not None:
                blocks.append(cur)
            cur = [line]
        elif cur is not None:
            if line.startswith("## ") or line.startswith("### "):
                blocks.append(cur)
                cur = None
            else:
                cur.append(line)
    if cur is not None:
        blocks.append(cur)
    if not blocks:
        return None
    last = blocks[-1]
    if "❌" not in last[0]:
        return None
    return "\n".join(last).strip()


def _retry_context(cwd, agent_type):
    """Prior-failure context to inject for a retrying agent, or None.

    Resolves the locked in_progress task, reads its handoff (scoped to the
    locked subtask when applicable), and returns the most recent failure record.
    None when: the agent is not a retry-context agent, no task is locked, no
    handoff exists, or the latest attempt was not a failure.

    Fail-safe: any error → None. This probe is advisory and must never break the
    floor/reminder injection that is the hook's primary contract — a retry nudge
    that risks the safety floor is worse than none.
    """
    # Retry context is roster-driven (``retry: true`` rows — task-executor is
    # THE retry agent; explorer and the stdout-block agents dispatch fresh, so
    # injecting a stale failure record into a non-retry dispatch would mislead).
    roster = _roster()
    if roster is None:
        return None
    # Namespaced dispatches (conductor:task-executor) resolve their bare roster
    # key (agent_roster.canonical_name); the raw name only reaches the retry
    # gate in the unrostered fail-open case.
    agent_type = roster.canonical_name(agent_type) or agent_type
    if agent_type not in roster.retry_agents():
        return None
    try:
        locked = resolve_locked_task(cwd)
        if locked is None:
            return None
        track_dir, p, t, s = locked
        parts = []

        # (1) The failure-analyst modification, if the spine wrote one for this
        # retry (B.5). Checked FIRST and independent of the handoff block — a
        # modified retry must reach the executor even if the handoff is empty.
        modified = _modified_guidance_block(track_dir, p, t, s)
        if modified:
            parts.append(modified)

        # (1a) The [Conductor Amendment] block, if a replan verdict amended
        # spec.md before this retry (A3). Independent of the modification — a
        # replan retry may carry both. Tells the executor an AC was superseded.
        amendment = _amendment_guidance_block(track_dir, p, t, s)
        if amendment:
            parts.append(amendment)

        # (2) The latest ### Attempt ❌ handoff record (the plain retry nudge).
        from track_state.handoff import get_handoff_content
        content = get_handoff_content(track_dir, p, t, s)
        if content:
            block = _latest_failure_attempt(content)
            if block:
                parts.append(f"{_RETRY_LEAD}\n\n{block}")

        return "\n\n".join(parts) if parts else None
    except Exception:
        return None


def _resolve_locked_task_type(cwd):
    """The leading task-type tag (registry-cased) for the locked task, or ``None``.

    ``locked_task.resolve`` returns ``(track_dir, p, t, s)`` — no tag. To branch
    task-executor's registry block on *this* task's leading tag, read the locked
    task's ``task_type`` field straight out of ``track-state.json`` at the locked
    1-based coordinates (it's a typed mirror of the name's tag, derived once at
    construction by :func:`task_profiles.derive_task_type`). That field is
    **lowercased** (``derive_task_type`` lowercases), but the registry keys are
    Title-case (``Refactor``), so the resolved value is matched case-insensitively
    against :func:`TAG_VOCAB` and the registry-cased form is returned — so the
    downstream profile/workflow lookup hits the right row. Best-effort: any
    resolution/read/shape error → ``None`` (the block falls back to the generic
    vocab summary). Subtask tasks inherit the parent tag, so the parent task's
    ``task_type`` is read whether or not a subtask is locked.
    """
    from track_state import task_profiles as tp
    locked = resolve_locked_task(cwd)
    if locked is None:
        return None
    track_dir, p, t, _s = locked
    state_path = Path(track_dir) / "track-state.json"
    if not state_path.exists():
        return None
    import json
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phases = state.get("phases") or []
    if not (1 <= p <= len(phases)):
        return None
    tasks = phases[p - 1].get("tasks") or []
    if not (1 <= t <= len(tasks)):
        return None
    tt = tasks[t - 1].get("task_type")
    if not tt or tt == "default":
        return None
    # task_type is lowercased; the registry keys are Title-case. Match
    # case-insensitively so the profile/workflow lookup resolves the right row.
    for cand in tp.TAG_VOCAB():
        if cand.lower() == tt:
            return cand
    return None


def _resolve_active_shape(cwd):
    """The resolved workflow-shape name of the locked task's track, fail-open to
    ``default``.

    Reuses :func:`resolve_locked_task` — the same mechanism the leading-tag
    resolver uses — so per-task and per-track resolution share one path. The
    executor block surfaces the shape's gates + default workflow so §4.0/§5.0
    prose can defer to the track's paradigm (a shape dropping tdd/coverage means
    the executor owes neither; a tagless task follows the shape's workflow).
    """
    from track_state.workflow_shapes import resolve_shape
    try:
        locked = resolve_locked_task(cwd)
        if locked is None:
            return "default"
        state_path = Path(locked[0]) / "track-state.json"
        if not state_path.exists():
            return "default"
        import json
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return resolve_shape(state.get("workflow_shape"))
    except Exception:
        return "default"


def _tag_summary_rows():
    """Thin re-export of the single-home renderer: ``task_profiles.tag_summary_rows``.

    The renderer moved to the registry module so the SubagentStart injection and
    the code-assembled plan-refuter dispatch prompt
    (``dispatch.cmd_plan_refute_prompt``) share ONE row shape — two hand-kept
    renderers of the same vocab is exactly the drift class the
    check-contract-registry-sync lint exists to kill. Lazy import keeps the
    fail-open boundary tight (registry resolution can raise; the floor/reminder
    contract is primary).
    """
    from track_state.task_profiles import tag_summary_rows
    return tag_summary_rows()


def reviewer_block_flags():
    """``{canonical flag-name: kebab token the block emits}`` for surfaced flags.

    The drift lint (``check-contract-registry-sync.py``) asserts that every flag
    name a watched agent's prose references is a key here, and that the block
    emits the key's value — so prose that defers to a flag (``gates``,
    ``grounding``, ``over_tag_risk``, …) is guaranteed the data it names. The
    mapping is EXPLICIT, not ``name.replace('_', '-')``, because the values are
    rendering details: the positive form renders as ``gates=tdd,...`` /
    ``grounding=test`` prefixes, and ``over_tag_risk`` shortens to ``over-tag``.
    A unit test asserts each value IS emitted by the renderers, so this
    declaration can't drift from the block silently. Canonical underscored keys
    match how prose references the flags; the kebab/prefix values are the
    rendering detail.
    """
    return {
        "gates": "gates=",
        "grounding": "grounding=",
        "over_tag_risk": "over-tag",
    }


def _registry_context(agent_type, cwd):
    """The resolved registry-vocab block for an agent, or ``None``.

    This is the injection that data-drives the agent-prose layer the way the CLI
    layer already is: task-executor reads its own task's resolved leading-tag
    profile from here rather than from hardcoded prose, so a project overlay
    flows end-to-end with zero plugin edits. spec-reviewer and refuter audit tag
    membership — they get the same vocab with the review flags surfaced
    (:func:`_registry_for_reviewer`), so their audit prose points at flag names
    instead of restated literal sets. spec-planner is deliberately NOT injected
    here — it fetches the full catalog on demand via ``track-state registry-doc``
    (the full tag+shape tables are a tier-B join, not a small per-task bit).

    Fail-safe: any error → ``None``. This block is advisory and must NEVER break
    the floor/reminder/retry injection that is the hook's primary contract — a
    registry block that risks the safety floor is worse than none. Mirrors
    :func:`_retry_context`'s fail-open posture.

    Returns ``None`` for agents whose roster row lacks ``registry_injection:
    true`` (task-executor + spec-reviewer + refuter; spec-planner deliberately
    fetches the full catalog on demand via ``registry-doc`` — the tier-B join).
    """
    roster = _roster()
    if roster is None:
        return None
    # Canonicalize BEFORE the membership gate + branch compares: a namespaced
    # dispatch (conductor:refuter) must resolve the reviewer branch, not die at
    # the registry_agents() membership check (the 2026-09 incident shape).
    agent_type = roster.canonical_name(agent_type) or agent_type
    if agent_type not in roster.registry_agents():
        return None
    try:
        # A class-bound persona (rostered wrapper, registry_injection: true)
        # passes the membership gate above but needs the EXECUTOR block — its
        # resolved profile + gate sets — same as task-executor. executor_slot
        # maps only executor-class non-spine agents, so explorer/spec-reviewer
        # still fall through to their own branches (or None).
        if agent_type == "task-executor" or roster.executor_slot(agent_type):
            return _registry_for_executor(cwd)
        if agent_type in ("spec-reviewer", "refuter"):
            return _registry_for_reviewer()
    except Exception:
        return None
    return None


def _registry_for_reviewer():
    """spec-reviewer + refuter: full tag vocab with the review flags.

    The reviewers audit tag MEMBERSHIP — an over-tag (``over_tag_risk``)
    exemption applied to business logic. They reason over the same profile the
    dispatch layer reads, so the block surfaces each row's owed ``gates`` and
    ``grounding`` (plus ``over-tag`` when the row carries it). The reviewer's
    prose then points at the field NAME rather than restating which tags carry
    it — a restated literal set is the first thing to drift, and the producer
    side of the adversarial pair must read the same ground truth as the
    verifier. See :data:`reviewer_block_flags` for the lint-facing contract.
    """
    lines = [f"{_REGISTRY_LEAD}", "",
             "RESOLVED TASK-TYPE TAG VOCAB (audit membership; flags name the risk):"]
    lines.extend(f"  - {r}" for r in _tag_summary_rows())
    return "\n".join(lines)


def _registry_for_executor(cwd):
    """task-executor: this task's leading-tag profile + an on-demand workflow pointer.

    Resolves the locked task's leading tag and surfaces its resolved profile
    (route/gates/grounding). The ``workflow`` prose itself is NOT
    injected — it is large + conditional (only the leading tag needs it), so it
    is read on demand: when the profile carries a ``workflow``, emit a one-line
    POINTER telling the executor to fetch it via
    ``track-state registry-doc --tag <Tag>`` (tier B, not tier A — see the
    three-tier context model). A project overlay tag with a bespoke ``workflow``
    flows to the executor here (the pointer names it; the prose is fetched,
    never inlined). If no task/type resolves, emits the resolved gate
    summary derived from TAG_VOCAB (so the executor still sees the closed set
    rather than a hardcoded enumeration).
    """
    from track_state import task_profiles as tp
    lines = [f"{_REGISTRY_LEAD}"]
    tag = _resolve_locked_task_type(cwd)
    if tag is not None:
        prof = tp._profile(tag)  # noqa: SLF001
        lines.append("")
        lines.append(f"RESOLVED PROFILE for this task's leading tag [{tag}]:")
        lines.append(f"  - route: {prof.get('route', 'executor')}")
        lines.append(f"  - gates: {', '.join(tp.gates_of(tag))}")
        lines.append(f"  - grounding: {tp.grounding_of(tag)}")
        workflow = tp.workflow_for(tag)
        doc = tp.workflow_doc_for(tag)
        if doc:
            # Tier B pointer for the docfile form: the per-dispatch manifest
            # (WORKFLOW_FILE) names + resolves the docfile — the executor reads
            # it there; registry-doc --tag is the same render for humans/CLI.
            lines.append(
                f"  - workflow: present — docfile `{doc}` (your dispatch "
                f"manifest's WORKFLOW_FILE names it; "
                f"`track-state registry-doc --tag {tag}` renders it)"
            )
        elif workflow:
            # Tier B: large + conditional payload → pointer, not inline prose.
            # The executor fetches it with one Bash call and follows it verbatim.
            lines.append(
                f"  - workflow: present — run "
                f"`track-state registry-doc --tag {tag}` and follow that prose "
                f"verbatim instead of default TDD."
            )
        else:
            lines.append("  - workflow: (absent → default TDD, Steps 3-8)")
        # The tactical-refactor flag (§3.6c): true => the orchestrator dispatches
        # conductor:refactorer once after this task succeeds. The declarative form
        # of the [Refactor] name marker / CONDUCTOR_TASK_REFACTOR=1 env — a project
        # overlay tag with refactor: true surfaces here with zero plugin edits.
        refactor = tp.refactor_for(tag)
        if refactor:
            lines.append(
                "  - refactor: true — this task's leading tag opts into the "
                "tactical refactorer (§3.6c fires on SUCCESS without a [Refactor] "
                "name marker or CONDUCTOR_TASK_REFACTOR env)."
            )
        else:
            lines.append("  - refactor: false (no tactical refactorer; Step 5 mechanical refactor still runs in-task)")
    # Always surface the resolved gate sets so §5.0 is registry-driven too —
    # a project overlay tag's gate obligations are visible even when no task
    # resolves.
    lines.append("")
    lines.append("RESOLVED GATE SETS (F2/F3 gating reads these from the registry):")
    cov = [t for t in tp.TAG_VOCAB() if "coverage" in tp.gates_of(t)]
    lines.append(f"  - tags owing the coverage gate (F3): {', '.join(f'[{t}]' for t in cov) or '(none)'}")
    tdd = [t for t in tp.TAG_VOCAB() if "tdd" in tp.gates_of(t)]
    lines.append(f"  - tags owing the TDD gate (F2): {', '.join(f'[{t}]' for t in tdd) or '(none)'}")
    # The third line keeps the block's completeness property under the positive
    # form: every registered tag (including project-overlay tags) is named on
    # exactly its lines, so a no-task-resolved dispatch still shows the whole
    # closed vocab — the both-exempt class lands here (§1.5's fast-path set).
    neither = [t for t in tp.TAG_VOCAB()
               if not ({"tdd", "coverage"} & set(tp.gates_of(t)))]
    lines.append(f"  - tags owing neither F2 nor F3 (fast-path class): {', '.join(f'[{t}]' for t in neither) or '(none)'}")
    # The track's resolved shape — the portability axis. Which gates the track
    # enforces (a gate fires iff listed here AND the task's class owes it),
    # and the workflow a tagless task defaults to. Lets task-executor's §4.0/§5.0
    # defer to the track's paradigm: a shape dropping tdd/coverage (e.g.
    # migration) means the executor owes neither and follows the shape's workflow
    # for a tagless task instead of default TDD.
    from track_state.workflow_shapes import gates_for
    shape = _resolve_active_shape(cwd)
    lines.append("")
    lines.append("RESOLVED SHAPE for this track:")
    lines.append(f"  - shape: {shape}")
    lines.append(f"  - gates: {', '.join(gates_for(shape)) or '(none)'}")
    return "\n".join(lines)


def _reset_tripwire_counter(cwd, agent_type):
    """Reset the PreToolUse tripwire round-counter for a fresh executor dispatch.

    ``on-pre-tool-tripwire.py`` counts task-executor's rounds against the locked
    task; SubagentStart fires once per dispatch, so this is the natural reset
    point. A retry therefore starts the count at 0. Best-effort and scoped to
    the task-executor SLOT — task-executor itself plus any class-bound persona
    occupying its slot (``executor_slot``; without this a persona dispatch
    would inherit a stale counter and trip early) — any failure is non-fatal
    (the counter just starts stale, which biases toward tripping slightly
    early: safe).
    """
    roster = _roster()
    key = roster.canonical_name(agent_type) if roster else None
    if roster is not None:
        # Slot-aware: a persona (rostered wrapper bound via a tag row's
        # `agent` field) IS the executor for its dispatch.
        if key != "task-executor" and not roster.executor_slot(agent_type):
            return
    elif agent_type != "task-executor":
        return
    try:
        locked = resolve_locked_task(cwd)
        if locked is None:
            return
        track_dir, phase, task, subtask = locked
        sub = f"-{subtask}" if subtask is not None else ""
        path = Path(track_dir) / ".conductor" / TRIPWIRE_COUNT_TMPL.format(
            phase=phase, task=task, sub=sub)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _wave_active(track_dir):
    """Whether this track has an active wave ledger (any ``in_flight`` member).

    Lib-light inline of ``track_state.wave._is_active`` — the hook must not
    import the heavy wave graph for one JSON read. Missing/corrupt ledger →
    ``False`` (no wave). Never raises.
    """
    try:
        path = Path(track_dir) / ".conductor" / "parallel.json"
        ledger = json.loads(path.read_text())
        return isinstance(ledger, dict) and any(
            isinstance(m, dict) and m.get("status") == "in_flight"
            for m in ledger.get("wave", []))
    except Exception:
        return False


def _stamp_inflight(track_dir, phase, task, subtask, agent_type):
    """Stamp the inflight marker for a single-writer agent that just SPAWNED.

    The marker means "spawned", not "prepared" (the 2026-09-01 dispatch-deadlock
    incident — see ``lib/dispatch_inflight`` for the full record):
    ``prepare_dispatch`` no longer writes it, making this the single production
    writer. Stamped only for rostered single-writer agents on the serial spine —
    wave members carry their own ``wave-agent.marker`` state and the wave F1
    guards own concurrency there, so an active wave suppresses the stamp (a wave
    member's serial-spine marker would poison the guard for the drain path).

    Namespace-aware (``conductor:task-executor`` → ``task-executor``) via
    ``agent_roster.canonical_name`` — same normalization as the dedupe hook that
    later READS the marker. Best-effort: any failure is swallowed — a stamp
    problem must never break a spawn (fail-open; worst case the guard sees no
    marker and allows, the pre-marker behavior).
    """
    try:
        from track_state import agent_roster
        key = agent_roster.canonical_name(agent_type)
        if key is None or key not in agent_roster.single_writers():
            return
        if _wave_active(track_dir):
            return
        from lib import dispatch_inflight as _inflight
        _inflight.stamp(track_dir, phase, task, subtask)
    except Exception:
        pass


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    cwd = input_data.get("cwd") or str(Path.cwd())

    # Lifecycle telemetry: record that a subagent started for the locked task
    # (if any). The `start` line is one half of the join — paired with the
    # `stop` from on-subagent-stop and the `probe` from on-dispatch-dedupe, a
    # grep over dispatch-lifecycle.log disambiguates a relapse's failure shape
    # (concurrent vs re-derive vs hook-not-firing). Best-effort; never raises.
    try:
        locked = resolve_locked_task(cwd)
        if locked is not None:
            _td, p, t, s = locked
        else:
            p = t = s = None
        # Stamp the inflight marker at spawn BEFORE the gen read below, so the
        # `start` event logs the generation THIS spawn stamped (not the stale
        # prior one) — probe/start sharing a gen is the telemetry join.
        if locked is not None:
            _stamp_inflight(_td, p, t, s, agent_type)
        # Read the inflight marker's gen so a start event records WHICH dispatch
        # generation is now running — same gen as the probe = the dispatch the
        # guard saw; a higher gen than a prior start = a spine re-dispatch.
        gen = "-"
        if locked is not None:
            try:
                from lib import dispatch_inflight as _inflight
                g = _inflight.read_gen(_td, p, t, s)
                gen = str(g) if g else "-"
            except Exception:
                pass
        lifecycle.emit(
            event="start", session=lifecycle.session_token(input_data, fallback=str(_td) if locked is not None else ""),
            agent=agent_type, phase=p, task=t, subtask=s, gen=gen,
        )
    except Exception:
        pass

    # Reset the round tripwire counter for a fresh task-executor dispatch.
    _reset_tripwire_counter(cwd, agent_type)

    # Get the reminder for this agent type from the merged roster (the fence
    # row). Unrostered → no reminder → fast no-op (fail-open: the agent
    # dispatches, it just gets no conductor scaffold).
    roster = _roster()
    reminder = roster.reminder_for(agent_type) if roster is not None else None

    if not reminder:
        # Unrostered agent type — emit no context (the roster gates this).
        write_simple_output()
        return

    # Safety floor first, then the result-format reminder, then the resolved
    # registry-vocab block (the data-driven agent-prose layer), then any retry-
    # context nudge (advisory; None for fresh tasks and non-retry agents). Order
    # matters: the floor must lead; the reminder precedes the registry block,
    # which precedes any appended retry block (floor < reminder < registry < retry).
    floor = _load_safety_floor()
    registry = _registry_context(agent_type, cwd)
    retry = _retry_context(cwd, agent_type)
    parts = [p for p in (floor, reminder, registry, retry) if p]
    write_simple_output(additional_context="\n\n".join(parts))


if __name__ == "__main__":
    main()