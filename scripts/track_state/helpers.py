"""Utility functions shared across track-state modules."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


from .core import load, save
from .constants import (
    MARKER_MAP, SHA_MARKERS, TERMINAL_STATUSES,
    TERMINAL_FOR_PARENT, AUTO_COMPLETE_OK, _RE_TRAILING_MARKER, _RESET_FIELDS,
    LOCKED_AT_FIELD,
)

# Closed vocabulary of dispatch tags (TDD/routing exemptions). This is now a
# *mirror* of the registry at templates/workflow/task-type-profiles.json, loaded
# via :mod:`track_state.task_profiles` — adding a tag is a registry row, not an
# edit here. Both ``extract_tags`` and ``strip_tags`` build their regexes from
# the live :func:`_tag_vocab` call so the registry IS the vocab — including any
# project overlay. Keep the tag table in runtime/contracts/plan-format-contract.md
# in sync (a wiring test guards drift).
from .task_profiles import TAG_VOCAB as _tag_vocab


def _resolve_conductor_root(track_dir):
    """Walk up from ``track_dir`` to the conductor root (the dir holding tracks.md).

    Returns the conductor root ``Path``, or ``None`` when no ancestor contains
    ``tracks.md`` — the fail-open signal that tells ``cmd_preflight`` to skip the
    workflow-files check rather than guess a location. Standard track layout is
    ``conductor/tracks/<name>``, so the ancestor two levels up (``conductor/``)
    is the root; walking is robust to nesting depth and to relative paths.
    """
    try:
        p = Path(track_dir).resolve(strict=False)
    except OSError:
        return None
    for cand in (p, *p.parents):
        if (cand / "tracks.md").exists():
            return cand
    return None


def _find_registry(start=None):
    """Locate ``conductor/tracks.md`` by walking up from ``start`` (default CWD).

    ``_resolve_conductor_root`` walks up from a TRACK dir looking for a directory
    that *holds* ``tracks.md`` — so it works when handed a track path, but NOT
    when handed the project root (where ``tracks.md`` is a child at
    ``conductor/tracks.md``). This locator checks BOTH ``<cand>/conductor/
    tracks.md`` and ``<cand>/tracks.md`` at each ancestor, so it resolves from
    the project root, from ``conductor/``, from a track dir, or from a nested
    subdir like ``src/auth/``. Returns the registry ``Path`` or ``None``.
    """
    try:
        p = Path(start or Path.cwd()).resolve(strict=False)
    except OSError:
        return None
    for cand in (p, *p.parents):
        for cand_root in (cand / "conductor", cand):
            f = cand_root / "tracks.md"
            if f.is_file():
                return f
    return None


def flag(args, name):
    """Parse a --flag value from args list. Supports --flag=val and --flag val."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return None


def flags_all(args, name):
    """All values for a repeatable --flag, in order (supports --flag val and --flag=val).

    Unlike ``flag`` (first match only), returns every occurrence — for repeatable
    flags such as write-result's ``--deviation``. Empty list if absent.
    """
    vals = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == name and i + 1 < len(args):
            vals.append(args[i + 1])
            i += 2
            continue
        if a.startswith(name + "="):
            vals.append(a[len(name) + 1:])
        i += 1
    return vals


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _display_loc(pi, ti, si=None):
    """Format 1-based indices as display string: P1.T3 or P1.T3.S2."""
    loc = f"P{int(pi)}.T{int(ti)}"
    if si is not None:
        loc += f".S{int(si)}"
    return loc


# Per-command allowlists for default-compact envelopes. "Compact" is pure
# subtraction: each dispatch-loop command keeps only the fields the orchestrator
# consumes (skills/implement/SKILL.md); --full restores the complete envelope.
# Adding a key to a tuple means the skill now relies on it, so update the
# consumer before, not after, emitting it.
COMPACT_FIELDS = {
    "next": ("phase", "task", "subtask", "name", "execution_mode"),
    "recover": ("status", "phase", "task", "subtask", "name",
                "retry_count", "max_retries", "phase_checkpoint_pending",
                "execution_mode", "fixes_applied", "decision"),
    "dispatch-next": ("action", "phase", "task", "subtask", "name",
                      "execution_mode",
                      # Rail A/B unification: the pre-assembled dispatch envelope
                      # (agent + prompt + attempt/max_retries for executor/explorer
                      # dispatch; wave for the phase-checker verifier fan-out) so
                      # skills/implement/SKILL.md §3.2/§3.3/§3.4 paste `prompt`
                      # verbatim instead of re-deriving KEY=value lines. Mirrors
                      # the `step` allowlist (Rail B), one source for both rails.
                      "agent", "prompt", "attempt", "max_retries",
                      "wave", "retry_count"),
    "dispatch-prepare": ("action", "phase", "task", "subtask", "name",
                         "sha", "is_resume", "retry_count",
                         "max_retries", "execution_mode"),
    "dispatch-finalize": ("status", "sha", "code_sha", "deviations", "committed",
                          "phase_checkpoint_pending", "retry_count",
                          "phase", "task", "subtask", "summary",
                          "coverage_gate", "tdd_gate", "coverage_pct",
                          "ac_integrity_gate", "ears_gate", "tc_consistency_gate"),
    # Rail B-min spine (skills/implement-step/SKILL.md). `step` collapses the
    # §2.0/§3.0 routing into one leaf action; the union of keys across its
    # action variants: dispatch (agent/prompt/attempt/is_resume), dispatch_batch
    # (phase/wave — the pre-assembled ac-tracer + test-runner verifier prompts),
    # ask (decision), skip_analyze/wave_active (phase), done, parent_stuck (sha).
    # Error envelopes bypass the allowlist (emit's error carve-out).
    "step": ("action", "phase", "task", "subtask", "name", "execution_mode",
             "agent", "prompt", "attempt", "max_retries", "is_resume",
             "decision", "sha", "wave",
             # Third-axis (workflow-shapes) disclosure: surfaced when a dispatch
             # agent is outside the resolved shape's topology (no-silent-caps).
             "shape_violation", "workflow_shape",
             "reason", "recommendation", "reasoning", "impact", "evidence",
             # failure-analyst halt (B): surfaces category + modification (the
             # proposed AC correction / task split / different approach) + what
             # was done, so the orchestrator can relay the diagnosis to a human;
             # `recovery` is the safe manual recipe (preserve commit on decompose,
             # edit ACs on replan) so the halt is actionable, not a dead end.
             "category", "modification", "what_was_done", "recovery"),
    # Wave parallelism (skills/parallel/SKILL.md). dispatch-wave carries the
    # member list (worktree/branch/worktree_track_dir) so the orchestrator can
    # fan out pinned task-executor agents; `deferred` surfaces the eligible-but-
    # capped members the skill must announce (no-silent-caps); `ineligible`
    # surfaces the per-task reason each pending task was rejected on a
    # no_ready_tasks (no-silent-X: tells the author WHICH gate excluded each
    # candidate — subtasked / non_executor / no_deps_comment / deps_unsatisfied);
    # wave-active refusal reuses `wave`.
    "dispatch-wave": ("action", "phase", "base_sha", "wave", "deferred",
                      "reason", "ineligible"),
    "wave-status": ("active", "phase", "base_sha", "members"),
    # wave-finalize emits the _finalize_task result envelope PLUS its own keys;
    # subset of dispatch-finalize's since the transition is shared.
    "wave-finalize": ("action", "status", "sha", "code_sha", "retry_count", "committed",
                      "phase", "task", "subtask", "summary", "deviations",
                      "member_status", "drained", "phase_checkpoint_pending"),
    "wave-abort": ("action", "aborted", "ok"),
    # Rail B-min wave spine (skills/parallel-step/SKILL.md). `wave-step` collapses
    # the dispatch-wave + wave-finalize loop into one leaf action; the union of
    # keys across its action variants: dispatch_batch (wave/deferred/base_sha +
    # is_resume/attempt on a single-member re-dispatch), wave_integrate
    # (phase/task/name), seam_review (finalized_count/revision_range), serial
    # (ineligible/execution_mode), phase_checkpoint (phase), ask (decision),
    # skip_analyze. Each `wave` member carries its own pre-assembled `prompt`.
    # Error envelopes bypass the allowlist (emit's error carve-out).
    "wave-step": ("action", "phase", "task", "subtask", "name", "execution_mode",
                  "base_sha", "wave", "deferred", "ineligible",
                  "finalized_count", "revision_range", "decision",
                  "is_resume", "attempt"),
    # Rail B-min post-loop spine (skills/post-loop-step/SKILL.md). Collapses the
    # prose post-loop (templates/post-loop.md §5.0–§8.0) into one leaf per call.
    # Union of keys across action variants: deferred_ask/archive_ask (decision +
    # phase/task/name identity), finalize (post = sync-plan + registry-update +
    # commit lines), dispatch/dispatch_advisory (agent + pre-assembled prompt),
    # the §6.0 advisory / §6.5 lint / §7.5 digest leaves also carry `post` (the
    # sidecar MERGE-stamp) + `post_on` ("always" — non-blocking gates advance on
    # any return; default "non_failure" gates the §7.0 review stamp), the §7.0
    # review dispatch also carries `range` + `shas_count`, digest (digest),
    # halt/error. `track_dir` so the skill has the path without re-resolving.
    # Error envelopes bypass the allowlist.
    "post-loop-step": ("action", "phase", "task", "subtask", "name",
                       "agent", "prompt", "decision", "post", "post_on",
                       "range", "shas_count", "digest", "reason", "incomplete",
                       "track_dir"),
}


def emit(obj, command, compact=True):
    """Emit obj as JSON, filtered to the command's compact allowlist by default.

    Output always stays a single JSON object (the test harness json.loads it),
    so --full falls back to the whole envelope rather than a non-JSON pipe form.
    Error envelopes (carrying an ``error`` key or ``status == "error"``) bypass
    the allowlist — diagnostics must survive compaction, not be stripped to an
    empty object.
    """
    if compact and "error" not in obj and obj.get("status") != "error":
        out({k: obj[k] for k in COMPACT_FIELDS[command] if k in obj})
    else:
        out(obj)


def _index_map(state):
    """Build a compact index→name map for error messages. Uses 1-based display."""
    lines = []
    for pi, ph in enumerate(state.get("phases", []), 1):
        lines.append(f"  Phase {pi}: {ph.get('name', '?')}")
        for ti, tk in enumerate(ph.get("tasks", []), 1):
            status = tk.get("status", "?")
            lines.append(f"    Task {ti}: [{status}] {tk.get('name', '?')}")
            for si, sub in enumerate(tk.get("subtasks", []), 1):
                ss = sub.get("status", "?")
                lines.append(f"      Subtask {si}: [{ss}] {sub.get('name', '?')}")
    return "\n".join(lines)


def target(state, p, t, s=None):
    try:
        task = state["phases"][p - 1]["tasks"][t - 1]
    except IndexError:
        n_phases = len(state.get("phases", []))
        idx_info = _index_map(state)
        if p < 1 or p > n_phases:
            raise IndexError(
                f"Phase index {p} out of range (track has {n_phases} phases). "
                f"Run 'track-state validate --fix' to correct state.\n"
                f"Available indices:\n{idx_info}") from None
        n_tasks = len(state["phases"][p - 1].get("tasks", []))
        raise IndexError(
            f"Task index {t} out of range in phase {p} (has {n_tasks} tasks). "
            f"Run 'track-state validate --fix' to correct state.\n"
            f"Available indices:\n{idx_info}") from None
    if s is not None and "subtasks" in task:
        try:
            return task["subtasks"][s - 1]
        except IndexError:
            n_subs = len(task["subtasks"])
            idx_info = _index_map(state)
            raise IndexError(
                f"Subtask index {s} out of range in P{p}.T{t} "
                f"(has {n_subs} subtasks). "
                f"Run 'track-state validate --fix' to correct state.\n"
                f"Available indices:\n{idx_info}") from None
    return task


def clean(tgt, keep):
    for k in _RESET_FIELDS:
        if k not in keep:
            tgt.pop(k, None)


def _normalize_sha(sha):
    """Normalize a git SHA to 7-char short form for consistent storage."""
    if not sha or not isinstance(sha, str):
        return ""
    sha = sha.strip()
    if not re.match(r"^[0-9a-f]+$", sha):
        return ""
    return sha[:7]


def extract_tags(name):
    """Extract task tags like [Docs], [Config] from name.

    Tags should be at the start or end of the task name, not in the middle.
    HTML comments are stripped before matching to avoid false positives
    from tag-like text inside <!-- ... --> annotations.
    Returns unique tags in the order they appear.
    """
    if not name:
        return []
    # Strip HTML comments to prevent false-positive matches from AC/TC
    # annotations. re.DOTALL so multi-line <!-- ... --> comments are stripped
    # whole — without it a newline inside the comment leaves tag-like text
    # (e.g. [Config]) sitting in the name to false-positive below.
    clean_name = re.sub(r'<!--.*?-->', '', name, flags=re.DOTALL)
    # Use lookahead/lookbehind to avoid consuming whitespace between consecutive
    # tags. The alternation is built from the LIVE registry vocab (not a frozen
    # import-time constant) so a tag a project overlay adds is recognized — the
    # per-call rebuild is deliberate, since the vocab can grow with an overlay
    # loaded after this module imported. (Caching the pattern here would miss
    # overlay tags registered mid-process.)
    pattern = r'(?<!\S)\[(' + '|'.join(_tag_vocab()) + r')\](?!\S)'
    matches = re.findall(pattern, clean_name)
    # Extract tag names and preserve order while removing duplicates
    seen = set()
    result = []
    for tag in matches:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def strip_tags(name):
    """Remove all dispatch tags ([Docs], [Config], …) from a name.

    The tag-stripping counterpart of ``extract_tags``, built from the same
    live registry vocab so callers that need a tag-insensitive identity
    key (e.g. ``reconcile``) don't re-declare the regex.
    """
    if not name:
        return ""
    pattern = r'(?<!\S)\[(?:' + '|'.join(_tag_vocab()) + r')\](?!\S)'
    return re.sub(pattern, '', re.sub(r'<!--.*?-->', '', name, flags=re.DOTALL)).strip()


def _inherit_tags(sub_tags, parent_name):
    """Return sub_tags if non-empty, otherwise inherit from parent task name."""
    return sub_tags if sub_tags else extract_tags(parent_name)


def conductor_dir(track_dir):
    d = Path(track_dir) / ".conductor"
    d.mkdir(exist_ok=True)
    return d


def _propagate_to_subtasks(tgt, status, reason_key, reason_value):
    """Propagate status to non-terminal subtasks when parent enters terminal state.

    Preserves 'failed' subtasks — their audit trail (retry_count,
    last_failure_summary) must not be destroyed by parent-level operations.
    """
    if "subtasks" not in tgt:
        return
    for sub in tgt["subtasks"]:
        if sub["status"] not in TERMINAL_FOR_PARENT:
            sub["status"] = status
            sub[reason_key] = reason_value


def _clean_trailing_markers(text):
    """Iteratively remove trailing [sha], [sha1,sha2,...], [N/A], [verified] markers."""
    prev = text
    while True:
        cleaned = _RE_TRAILING_MARKER.sub('', prev)
        if cleaned == prev:
            return prev
        prev = cleaned


def _safe_task_name(state, phase_idx, task_idx):
    """Safely get task name from state, returning '...' on any index error."""
    try:
        if not state or phase_idx < 1 or task_idx < 1:
            return '...'
        phases = state.get('phases', [])
        if phase_idx > len(phases):
            return '...'
        tasks = phases[phase_idx - 1].get('tasks', [])
        if task_idx > len(tasks):
            return '...'
        return tasks[task_idx - 1].get('name', '...')
    except (IndexError, KeyError, TypeError):
        return '...'


def _is_phase_terminal(phase):
    """Check if all tasks and subtasks in a phase are in terminal status."""
    for task in phase.get("tasks", []):
        if task["status"] not in TERMINAL_FOR_PARENT:
            return False
        for sub in task.get("subtasks", []):
            if sub["status"] not in TERMINAL_FOR_PARENT:
                return False
    return True


def _last_subtask_sha(task):
    """Return the commit_sha of the last completed subtask, or empty string."""
    for sub in reversed(task.get("subtasks", [])):
        sha = sub.get("commit_sha", "")
        if sha:
            return sha
    return ""


def _store_evidence(state, track_dir, p, t, s, r):
    """Write evidence from result onto the completed task/subtask node."""
    tgt = target(state, int(p), int(t), int(s) if s is not None else None)
    tgt["evidence"] = {
        "coverage_pct": r.get("coverage_pct"),
        "tc_coverage": r.get("tc_coverage", ""),
        "deviations": len(r.get("spec_deviation_detail", [])),
    }
    save(track_dir, state)


def _extract_tags_for_task(state, phase_str, task_str):
    """Extract tags from task name for gate exemption checks."""
    try:
        pi, ti = int(phase_str), int(task_str)
        task = state["phases"][pi - 1]["tasks"][ti - 1]
        return extract_tags(task["name"])
    except (IndexError, KeyError, ValueError):
        return []



def _tag_exempt_from_coverage(tags):
    """Tags that don't require coverage gate enforcement."""
    from .task_profiles import is_coverage_exempt
    return is_coverage_exempt(tags)



def _tag_exempt_from_tdd(tags):
    """Tags that don't require TDD gate enforcement."""
    from .task_profiles import is_tdd_exempt
    return is_tdd_exempt(tags)



def _resolve_gate_groups(plan_path):
    """Re-parse plan.md headings → ``{group_name: [phase_numbers_in_order]}``.

    Cross-phase gate groups (``<!-- gate_group: <name> -->`` on a ``## Phase N:``
    heading) are advisory metadata re-parsed at every read — NOT persisted to
    track-state.json (see plan_parse._extract_gate_group). This helper is the
    re-parse-on-read that replaces persistence: the checkpoint gate and the
    terminal-gate stamp path both call it to learn a phase's group membership.

    Returns ``{}`` when plan.md is missing/unreadable (fail-open = no groups =
    every phase gates itself normally). Group names are lowercased by the
    parser, so keys are lowercase. Member phase numbers are in declaration order
    (which equals ascending order for a valid contiguous group; the parser's
    ``validate_gate_groups`` warns on non-contiguity separately).

    The **terminal** member of a group is ``members[-1]``.
    """
    groups = {}
    try:
        text = Path(plan_path).read_text()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return groups
    # Mirror plan_parse._extract_gate_group: a comment whose body starts with
    # ``gate_group:`` carries a lowercased single-token name. We re-parse here
    # (rather than import plan_parse's extractor) to stay decoupled from the
    # full parse and to read only the heading lines we need.
    for m in re.finditer(r"^##\s+Phase\s+(\d+)\b.*$", text, re.MULTILINE):
        heading = m.group(0)
        pnum = int(m.group(1))
        for cm in re.finditer(r"<!--(.*?)-->", heading, re.DOTALL):
            body = cm.group(1)
            if not re.match(r"^\s*gate_group\s*:", body, re.IGNORECASE):
                continue
            payload = re.sub(r"^\s*gate_group\s*:", "", body, count=1,
                             flags=re.IGNORECASE)
            tokens = [t for t in payload.split() if t]
            if len(tokens) == 1:
                groups.setdefault(tokens[0].lower(), []).append(pnum)
    return groups


def _phase_gate_group_membership(track_dir, phase_index):
    """Return ``(group_name, is_terminal_member)`` for a phase, or ``(None, False)``.

    Resolves gate groups from plan.md and reports whether ``phase_index`` is a
    member of any group, and if so whether it is that group's terminal (last)
    member. Used by ``_phase_needs_checkpoint`` to decide deferral:
    - non-terminal member → defer (the terminal member gates the group).
    - terminal member     → gate normally (it gates the whole group's diff).
    - not in any group    → normal per-phase gate (the default).
    """
    plan_path = Path(track_dir) / "plan.md"
    groups = _resolve_gate_groups(plan_path)
    for gname, members in groups.items():
        if phase_index in members:
            return gname, (members[-1] == phase_index)
    return None, False


def _terminal_gate_group_members(track_dir, phase_index):
    """Return the member phase numbers of ``phase_index``'s gate_group if it is
    the terminal member, else ``[]``.

    Called by ``cmd_phase_checkpoint_review`` on a terminal PASS to stamp every
    member (non-terminal members carry a ``[checkpoint: deferred <group>]``
    marker that must trade for a real SHA). Returns ``[]`` for a non-terminal
    member or an ungrouped phase — the caller then stamps only ``phase_index``.
    """
    plan_path = Path(track_dir) / "plan.md"
    groups = _resolve_gate_groups(plan_path)
    for _gname, members in groups.items():
        if phase_index in members and members[-1] == phase_index:
            return list(members)
    return []


def _phase_needs_checkpoint(track_dir, state, phase_index):
    """Check if a phase needs a checkpoint (all tasks done, no checkpoint in plan.md).

    Returns phase index if checkpoint is needed, None otherwise.

    Cross-phase gate groups (plan-format-contract.md §"Phase Gate Groups"):
    a NON-TERMINAL member of a gate_group defers — its tasks may be terminal
    but the phase is intentionally red (a later phase closes the debt), so it
    must NOT gate or fan out verifiers. The deferral is recorded by a
    ``[checkpoint: deferred <group>]`` stamp so the marker is visible and this
    function reads as "checkpoint present" on re-read (idempotent). The
    terminal member gates normally and, on PASS, stamps every member with a
    real SHA (dispatch.cmd_phase_checkpoint_review).
    """
    # Skip invalid phase indices
    if phase_index < 1:
        return None

    try:
        phase = state["phases"][phase_index - 1]
    except (IndexError, KeyError):
        return None

    # Check if all tasks in phase are in terminal state
    terminal = TERMINAL_FOR_PARENT
    for task in phase.get("tasks", []):
        if task.get("status") not in terminal:
            return None
        for sub in task.get("subtasks", []):
            if sub.get("status") not in terminal:
                return None

    # Check if checkpoint exists in plan.md
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return phase_index  # No plan, needs checkpoint

    try:
        content = plan_path.read_text()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return phase_index

    # Check for a checkpoint marker on this phase's heading. Two kinds now
    # coexist, both of which read as "checkpoint present" (skip the gate):
    #   [checkpoint: <sha>]            — a real PASSED stamp
    #   [checkpoint: deferred <group>] — a gate_group member deferring to its
    #                                    terminal member (still red-on-purpose)
    pattern = rf"^##\s+Phase\s+{phase_index}\b.*\[checkpoint:\s+(?:deferred\s+\S+|[0-9a-f]+)\]"
    if re.search(pattern, content, re.MULTILINE):
        return None  # Checkpoint (real or deferred) exists

    # Gate-group deferral: a non-terminal member whose tasks are all terminal
    # defers — it's red on purpose and a later phase gates the group. Stamp the
    # deferral so the marker is visible and this function stays idempotent on
    # re-read (the regex above then skips it). The terminal member falls through
    # to the normal "needs checkpoint" return below.
    group_name, is_terminal = _phase_gate_group_membership(track_dir, phase_index)
    if group_name is not None and not is_terminal:
        # Lazy import: misc.py imports helpers.py at module load, so a top-level
        # ``from .misc import ...`` here would create a circular import. The
        # stamp is a misc concern (plan.md file mutation); the deferral *decision*
        # is a helpers concern. Resolving at call time keeps both true.
        from .misc import _stamp_deferred_checkpoint_in_plan
        _stamp_deferred_checkpoint_in_plan(track_dir, phase_index, group_name)
        return None  # deferred — no gate, no verifier fan-out

    return phase_index  # Phase done but no checkpoint (terminal member or ungrouped)


def _any_phase_needs_checkpoint(track_dir, state):
    """Check if any phase needs a checkpoint. Returns first phase index that needs one, or None."""
    for pi, _phase in enumerate(state.get("phases", []), 1):
        if _phase_needs_checkpoint(track_dir, state, pi) is not None:
            return pi
    return None


def _reset_task(tgt):
    """Reset a single task/subtask dict to pending, clearing all completion fields."""
    tgt["status"] = "pending"
    for k in _RESET_FIELDS:
        tgt.pop(k, None)


def _reset_lock_reap(tgt):
    """Reap a stale in_progress lock: flip status→pending, KEEP task history.

    Distinct from :func:`_reset_task` (full reset for the explicit ``reset``
    command, which legitimately wipes ``retry_count``/``commit_sha``): a
    stale-LOCK reap is RECOVERY from a killed/paused session, not a reset. The
    attempt wasn't abandoned — ``retry_count``/``last_failure_summary``/
    ``commit_sha`` are intrinsic task history and must survive so the
    re-dispatched attempt still counts against the per-task budget. A session
    clear is a pause, not a crash. Mirrors the preservation contract of
    ``mutations.reactivate_for_modified_retry``.

    ``locked_at`` is dropped explicitly: a reaped task is no longer in_progress,
    so advertising a stale lock heartbeat is misleading, and this function only
    re-reaps ``status == "in_progress"`` tasks, so dropping it is safe.
    """
    tgt["status"] = "pending"
    tgt.pop(LOCKED_AT_FIELD, None)
    clean(tgt, {"status", "retry_count", "last_failure_summary", "commit_sha"})
