#!/usr/bin/env python3
"""SubagentStart hook: inject the safety floor + result-format reminder.

Reads hook input from stdin, outputs JSON with additionalContext. For every known
Conductor subagent the output is: the universal safety floor
(``runtime/subagent-firewall.md``), then the agent's result-format reminder.
Unknown agent types get no context (the SubagentStart matcher gates which agents
fire this hook at all).

For retry-context agents (task-executor), a third piece is appended when the
locked task has a prior failed attempt: the most recent ``### Attempt ❌`` record
from its handoff. This is the deterministic, can't-be-skipped counterpart to the
agent's own Layer 3.R load — even if the orchestrator under-reports retry status,
the prior failure reason / suggested next step reaches the retry agent here.
"""

import functools
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


# Result-format reminders. Agent markdown files already define full role behavior;
# only the delimiter format is reinforced here — filter-subagent-output.py depends
# on it being present in the subagent's emitted output.
AGENT_REMINDERS = {
    "task-executor": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "code-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "explorer": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "phase-checker": "[Conductor] Result format: ---CHECKPOINT RESULT--- ... ---END RESULT---",
    "ac-tracer": "[Conductor] Result format: ---AC TRACE RESULT--- ... ---END RESULT---",
    "build-runner": "[Conductor] Result format: ---BUILD VERIFY RESULT--- ... ---END RESULT---",
    "test-runner": "[Conductor] Result format: ---L1 VERIFY RESULT--- ... ---END RESULT---",
    "corpus-writer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
    "wiki-synthesizer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
    "doc-linter": "[Conductor] Result format: ---DOC LINT RESULT--- ... ---END RESULT---",
    "skip-analyst": "[Conductor] Result format: ---SKIP ANALYSIS--- ... ---END ANALYSIS---",
    "failure-analyst": "[Conductor] Result format: ---FAILURE ANALYSIS--- ... ---END ANALYSIS---",
    "spec-planner": "[Conductor] Result format: ---SPEC PLAN RESULT--- ... ---END SPEC PLAN RESULT---",
    "spec-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "project-analyzer": "[Conductor] Result format: ---ANALYSIS RESULT--- ... ---END ANALYSIS RESULT---",
    "wiki-differ": "[Conductor] Result format: ---WIKI DIFF RESULT--- ... ---END RESULT---",
    "wiki-researcher": "[Conductor] Result format: ---WIKI RESEARCH RESULT--- ... ---END RESULT---",
    "refuter": "[Conductor] Result format: ---REFUTATION RESULT--- ... ---END RESULT---",
    "command-digester": "[Conductor] Result format: keyed on PURPOSE — red|coverage → ---TEST DIGEST RESULT--- ... ---END RESULT---; log-verify → ---LOG CHECK RESULT--- ... ---END RESULT---",
    "doc-probe": "[Conductor] Result format: ---PROBE RESULT--- ... ---END RESULT---",
    "apply-fixes": "[Conductor] Result format: ---FIX RESULT--- ... ---END RESULT---",
    "refactorer": "[Conductor] Result format: ---REFACTOR RESULT--- ... ---END RESULT---",
    "strategy-writer": "[Conductor] Result format: ---STRATEGY RESULT--- ... ---END RESULT---",
}


# Agents whose re-dispatch carries prior-failure context. task-executor is THE
# retry agent (attempt 2+); explorer and the stdout-block agents are dispatched
# fresh, so they are excluded — injecting a stale failure record into a non-retry
# dispatch would mislead. Add here if another agent gains retry semantics.
_RETRY_AGENTS = {"task-executor"}

# Agents that receive the resolved registry-vocab block (task-type tags).
# This is how a project overlay's tags flow end-to-end to the agent-prose
# layer: task-executor is data-driven by injection here (its OWN task's leading-
# tag profile — small + per-task-resolved, tier A). spec-reviewer and refuter
# AUDIT tag membership — they receive the vocab WITH the review flags
# (over_tag_risk) so their prose can defer to the flags instead of restating
# which tags carry them (a restated set is the first thing to drift). spec-planner
# is NOT here: it needs the FULL tag catalog (a tier-B join), which it fetches on
# demand via `track-state registry-doc` (§3.1) — only the small/resolved bits stay
# injected. Add here if another agent should see the resolved vocab at dispatch.
_REGISTRY_AGENTS = {"task-executor", "spec-reviewer", "refuter"}

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
    if agent_type not in _RETRY_AGENTS:
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
    """One summary line per registered tag: ``[Tag] route tdd/coverage hint``.

    Reads the resolved registry (so project-overlay tags appear) and renders a
    compact row per tag. Lazy import: this module is imported transitively by
    the standalone hook scripts; resolving the registry can raise, and the
    fail-open boundary must stay tight (the floor/reminder contract is primary).
    """
    from track_state import task_profiles as tp
    rows = []
    for tag in tp.TAG_VOCAB():
        prof = tp._profile(tag)  # noqa: SLF001 — registry-internal profile lookup
        route = prof.get("route", "executor")
        flags = []
        if prof.get("tdd_exempt"):
            flags.append("tdd-exempt")
        if prof.get("coverage_exempt"):
            flags.append("coverage-exempt")
        if prof.get("over_tag_risk"):
            flags.append("over-tag")
        hint = tp.when_to_use_for(tag)
        flagstr = f" [{', '.join(flags)}]" if flags else ""
        hintstr = f" — {hint}" if hint else ""
        rows.append(f"[{tag}] route={route}{flagstr}{hintstr}")
        # Surface the tag's explicit `signals` keywords so the planner can match a
        # task description against them — the matcher DATA (tier-A), not the large
        # `workflow` prose (tier-B on-demand via registry-doc --tag). Only emitted
        # when the registry row EXPLICITLY declares `signals` (a list): tags like
        # [Refactor] deliberately omit it (opt-in, not goal-detected), so we must
        # not show the weaker tokens _signals_for would *derive* from when_to_use
        # (those are for derive_task_tag's coarse fallback, not for human/planner
        # signal-matching — showing them here would imply [Refactor] is matchable).
        sig = prof.get("signals")
        if isinstance(sig, list) and sig:
            rows.append(f"  signals: {', '.join(str(s) for s in sig)}")
    return rows


def reviewer_block_flags():
    """``{canonical flag-name: kebab token the block emits}`` for surfaced flags.

    The drift lint (``check-contract-registry-sync.py``) asserts that every flag
    name a watched agent's prose references is a key here, and that the block
    emits the key's value — so prose that defers to a flag (``over_tag_risk``,
    ``tdd_exempt``, …) is guaranteed the data it names. The mapping is EXPLICIT,
    not ``name.replace('_', '-')``, because one flag shortens in the block:
    ``over_tag_risk`` -> ``over-tag`` (the renderer's compact token). A unit test
    asserts each value IS emitted by the renderers, so this declaration can't
    drift from the block silently. Canonical underscored keys match how prose
    references the flags; the kebab values are the rendering detail.
    """
    return {
        "tdd_exempt": "tdd-exempt",
        "coverage_exempt": "coverage-exempt",
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

    Returns ``None`` for agents outside :data:`_REGISTRY_AGENTS`.
    """
    if agent_type not in _REGISTRY_AGENTS:
        return None
    try:
        if agent_type == "task-executor":
            return _registry_for_executor(cwd)
        if agent_type in ("spec-reviewer", "refuter"):
            return _registry_for_reviewer()
    except Exception:
        return None
    return None


def _registry_for_reviewer():
    """spec-reviewer + refuter: full tag vocab with the review flags.

    The reviewers audit tag MEMBERSHIP — an over-tag (``over_tag_risk``)
    exemption applied to business logic. They reason over the same flags the
    dispatch layer reads, so the block surfaces those flags per row (the tag row
    carries ``over-tag``). The reviewer's prose then points at the flag NAME
    rather than restating which tags carry it — a restated literal set is the
    first thing to drift, and the producer side of the adversarial pair must read
    the same ground truth as the verifier. See :data:`reviewer_block_flags` for
    the lint-facing contract.
    """
    lines = [f"{_REGISTRY_LEAD}", "",
             "RESOLVED TASK-TYPE TAG VOCAB (audit membership; flags name the risk):"]
    lines.extend(f"  - {r}" for r in _tag_summary_rows())
    return "\n".join(lines)


def _registry_for_executor(cwd):
    """task-executor: this task's leading-tag profile + an on-demand workflow pointer.

    Resolves the locked task's leading tag and surfaces its resolved profile
    (route/tdd_exempt/coverage_exempt). The ``workflow`` prose itself is NOT
    injected — it is large + conditional (only the leading tag needs it), so it
    is read on demand: when the profile carries a ``workflow``, emit a one-line
    POINTER telling the executor to fetch it via
    ``track-state registry-doc --tag <Tag>`` (tier B, not tier A — see the
    three-tier context model). A project overlay tag with a bespoke ``workflow``
    flows to the executor here (the pointer names it; the prose is fetched,
    never inlined). If no task/type resolves, emits the resolved exemption
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
        lines.append(f"  - tdd_exempt: {prof.get('tdd_exempt', False)}")
        lines.append(f"  - coverage_exempt: {prof.get('coverage_exempt', False)}")
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
    # Always surface the resolved exemption set so §5.0 is registry-driven too —
    # a project overlay tag's exemptions are visible even when no task resolves.
    lines.append("")
    lines.append("RESOLVED EXEMPTION SETS (F2/F3 gating reads these from the registry):")
    exempt = [t for t in tp.TAG_VOCAB() if tp._profile(t).get("coverage_exempt")]  # noqa: SLF001
    lines.append(f"  - coverage(F2/F3)-exempt tags: {', '.join(f'[{t}]' for t in exempt) or '(none)'}")
    tdd_exempt = [t for t in tp.TAG_VOCAB() if tp._profile(t).get("tdd_exempt")]  # noqa: SLF001
    lines.append(f"  - tdd(F2)-exempt tags: {', '.join(f'[{t}]' for t in tdd_exempt) or '(none)'}")
    # The track's resolved shape — the portability axis. Which gates the track
    # enforces (a gate fires iff listed here AND the task's tag is not exempt),
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
    """Reset the PreToolUse tripwire round-counter for a fresh task-executor dispatch.

    ``on-pre-tool-tripwire.py`` counts task-executor's rounds against the locked
    task; SubagentStart fires once per dispatch, so this is the natural reset
    point. A retry therefore starts the count at 0. Best-effort and scoped to
    task-executor — any failure is non-fatal (the counter just starts stale,
    which biases toward tripping slightly early: safe).
    """
    if agent_type != "task-executor":
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

    # Get reminder for this agent type
    reminder = AGENT_REMINDERS.get(agent_type, "")

    if not reminder:
        # Unknown agent type — emit no context (the matcher gates this in practice).
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