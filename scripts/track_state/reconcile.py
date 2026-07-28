"""``track-state reconcile-plan`` — safe post-edit reconciliation of plan.md → state.

Use case: the user did ``git reset`` to undo a task-executor run that diverged
from the constraint (large refactor / tech-stack-upgrade), then hand-edited
``plan.md`` — changed a task tag, split a task, reordered or reconstructed the
remaining tasks. They want ``track-state.json`` brought back in sync **without
losing ``commit_sha`` records on tasks whose work is still correct.**

Why this exists (the gap): the other plan→state paths are unsafe for this case —

  * ``sync-plan`` renders **state→plan positionally** (``sync.py:42-67``): reorder
    or insert a task above a completed one and the SHA silently rebinds to the
    wrong task. Its plan→state path only auto-absorbs *new subtask lines* and
    never propagates tag changes (tags are re-derived from the name at dispatch).
  * ``init-from-plan --force`` rebuilds state but **wipes every SHA** (V7).
  * ``reset`` destroys SHAs (``_RESET_FIELDS``).
  * ``split`` is the one SHA-preserving template (``_do_split`` keep-set); this
    module generalizes that keep-set discipline across all edit kinds.

The core invariant: matching is **by phase number + normalized task name**, never
by position. Dry-run is the default. No rename-vs-delete is ever guessed —
unmatched nodes are refused until resolved by an explicit flag. ``_do_sync_plan``
is never altered; this module calls it read-only-after-write only.
"""
import re
import subprocess
from pathlib import Path

from .constants import MARKER_MAP, SHA_MARKERS
from .core import load, transaction
from .helpers import (
    out, now_iso, strip_tags, _clean_trailing_markers,
)
from .plan_parse import parse_plan
from .sync import _do_sync_plan
from .git_ops import _git_commit_ensured


# status char → status name (inverse of MARKER_MAP). Built once.
_MARKER_TO_STATUS = {v: k for k, v in MARKER_MAP.items()}

# Status NAMES whose marker is SHA-bearing (terminal). Derived once from the
# constants so reconcile's four "is this node terminal-with-SHA?" checks share
# one set instead of rebuilding the comprehension per call.
_SHA_STATUSES = {name for name, char in MARKER_MAP.items() if char in SHA_MARKERS}


# A normalized, tag-insensitive identity key for matching plan-side and state-side
# nodes. Trailing markers ([sha]/[N/A]/[verified]) and dispatch tags are stripped
# first (the state-side name never carries markers; a freshly-edited plan line
# might carry either), then case-folded + whitespace-collapsed. Exact match
# required — NO fuzzy matching.
def _name_key(name):
    if not name:
        return ""
    stripped = strip_tags(_clean_trailing_markers(name).strip())
    return re.sub(r"\s+", " ", stripped).lower()


def _is_sha_live(track_dir, sha):
    """True iff ``sha`` names a commit reachable in ``track_dir``'s repo.

    Fail-open: any subprocess/git error → treat as live (don't block reconcile on
    a non-git track or a shallow clone). Only a definitive ``cat-file -e`` exit 1
    (object absent) counts as dangling.
    """
    if not sha or not re.match(r"^[0-9a-f]{7}$", sha):
        return True  # malformed/empty isn't "dangling" — leave it for validate
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(track_dir), capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def _loc(phase_num, task_idx, subtask_idx=None):
    """P{n}.T{n} or P{n}.T{n}.S{n} — the human-readable node address."""
    base = f"P{phase_num}.T{task_idx}"
    return base + (f".S{subtask_idx}" if subtask_idx else "")


def _state_index_maps(state):
    """Build name→index lookups for one reconcile pass.

    Returns ``(by_phase,)`` where each entry is::

        {
          "tasks": {task_name_key: [task_idx, task_dict]},
          "subs":  {task_name_key: {sub_name_key: [sub_idx, sub_dict]}},
        }

    Keyed by ``_name_key`` so plan/state match regardless of tag wording or
    case. ``task_idx``/``sub_idx`` are 1-based (the ``target()`` convention).
    """
    by_phase = {}
    for p_idx, phase in enumerate(state.get("phases", []), start=1):
        phase_entry = {"tasks": {}, "subs": {}}
        for t_idx, task in enumerate(phase.get("tasks", []), start=1):
            tk = _name_key(task.get("name", ""))
            phase_entry["tasks"][tk] = [t_idx, task]
            sub_map = {}
            for s_idx, sub in enumerate(task.get("subtasks", []), start=1):
                sk = _name_key(sub.get("name", ""))
                sub_map[sk] = [s_idx, sub]
            phase_entry["subs"][tk] = sub_map
        by_phase[p_idx] = phase_entry
    return by_phase


def _parse_resolution(specs):
    """Parse repeatable ``<phase>:<...>`` resolution flags into a structured map.

    Each spec is ``"<phase>:<task-or-task.subtask>"`` (for --drop / --clear-dangling)
    or ``"<phase>:<old>=<new>"`` (for --rename). ``<task>`` / ``<subtask>`` may be a
    1-based index or a name. Returns a list of ``(phase_int, parts)`` tuples where
    parts is ``{"drop": (task, subtask)}`` / ``{"clear": (task, subtask)}`` /
    ``{"rename": (old, new)}``. Raises ``ValueError`` on malformed specs.
    """
    parsed = []
    for spec in specs or []:
        if ":" not in spec:
            raise ValueError(
                f"bad resolution spec (expected '<phase>:<...>'): {spec!r}")
        phase_s, rest = spec.split(":", 1)
        try:
            phase = int(phase_s)
        except ValueError:
            raise ValueError(
                f"bad phase in resolution spec {spec!r} (expected integer phase)")
        parsed.append((phase, rest))
    return parsed


def _compute_reconciliation(track_dir, state, edited_plan, liveness=True, aliases=None):
    """Pure diff: classify every plan node + every SHA-bearing state node.

    ``edited_plan`` is the dict returned by :func:`plan_parse.parse_plan`
    (``{"phases": [...], "errors": [...], "warnings": [...]}``). ``track_dir`` is
    used only for the SHA-liveness probe (pass ``liveness=False`` to skip git).
    ``aliases`` is an optional ``{phase_num: {plan_name_key: state_name_key}}``
    map for ``--rename``: a renamed task matches its pre-rename state node while
    the new name is persisted via the tag_or_status bucket.

    Returns a dict with buckets::

        unchanged, tag_or_status, split, unmatched,
        dangling_sha, plan_errors

    Each entry in the action buckets is a dict with ``loc`` (P{n}.T{n}[.S{n}]),
    ``phase`` (int), a stable ``state_path`` ``(phase, task_idx, subtask_idx)``
    into the loaded state for the apply pass, and the before/after fields the
    apply step needs. This function does NO writes — dry-run prints it, tests
    assert it, and ``cmd_reconcile_plan`` applies it.
    """
    result = {
        "unchanged": [], "tag_or_status": [], "split": [],
        "unmatched": [], "dangling_sha": [], "plan_errors": list(edited_plan.get("errors", [])),
    }
    maps = _state_index_maps(state)
    touched_state_keys = set()  # (phase, task_idx[, subtask_idx]) matched by plan

    for ephase in edited_plan.get("phases", []):
        pnum = ephase["number"]
        phase_map = maps.get(pnum)
        if phase_map is None:
            # Phase number present in plan but not in state — every task here is
            # unmatched (refused). Shouldn't normally happen post-edit, but treat
            # it as a structural conflict rather than silently dropping.
            for ti, task in enumerate(ephase.get("tasks", []), start=1):
                result["unmatched"].append({
                    "loc": _loc(pnum, ti), "phase": pnum, "state_path": None,
                    "kind": "new_task_in_unknown_phase",
                    "name": task.get("name", ""), "detail": f"Phase {pnum} not in state",
                })
            continue

        for ti, task in enumerate(ephase.get("tasks", []), start=1):
            e_name = task.get("name", "")
            tk = _name_key(e_name)
            e_marker = task.get("_edited_status")  # stitched by _stitch_markers
            # --rename alias: match the renamed plan task against its pre-rename
            # state node so it flows through tag_or_status (persisting new name).
            alias_key = (aliases or {}).get(pnum, {}).get(tk)
            match = phase_map["tasks"].get(tk)
            if match is None and alias_key is not None:
                match = phase_map["tasks"].get(alias_key)
            if match is None:
                result["unmatched"].append({
                    "loc": _loc(pnum, ti), "phase": pnum, "state_path": None,
                    "kind": "new_task", "name": e_name,
                    "detail": "task in edited plan has no name match in state",
                })
                continue
            t_idx, stask = match
            touched_state_keys.add((pnum, t_idx, None))
            # The state-side parent key (aliased under rename) for subtask lookups.
            state_tk = alias_key if (alias_key is not None and match is not None) else tk

            # Subtasks in the edited plan under this task.
            for si, esub in enumerate(task.get("subtasks", []), start=1):
                esub_name = esub["name"] if isinstance(esub, dict) else esub
                esub_marker = esub.get("_edited_status") if isinstance(esub, dict) else None
                sk = _name_key(esub_name)
                sub_match = phase_map["subs"].get(state_tk, {}).get(sk)
                if sub_match is None:
                    # New subtask under a matched parent → SPLIT bucket.
                    result["split"].append({
                        "loc": _loc(pnum, t_idx, si), "phase": pnum,
                        "state_path": (pnum, t_idx, None),  # append to parent
                        "parent_name": stask.get("name", ""),
                        "new_subtask": esub_name,
                    })
                    continue
                s_idx, ssub = sub_match
                touched_state_keys.add((pnum, t_idx, s_idx))
                _classify_marker_change(
                    result, _loc(pnum, t_idx, s_idx), pnum,
                    (pnum, t_idx, s_idx), ssub, esub_marker, esub_name)

            # Any state-side subtask with no edited-plan match AND a commit_sha is
            # an unmatched removal (refused). Subtasks without a SHA are tolerated
            # as absorbed-then-dropped (sync-plan already handles re-absorption).
            for sk, (s_idx, ssub) in phase_map["subs"].get(state_tk, {}).items():
                if (pnum, t_idx, s_idx) in touched_state_keys:
                    continue
                if ssub.get("commit_sha"):
                    result["unmatched"].append({
                        "loc": _loc(pnum, t_idx, s_idx), "phase": pnum,
                        "state_path": (pnum, t_idx, s_idx),
                        "kind": "dropped_subtask_with_sha",
                        "name": ssub.get("name", ""),
                        "commit_sha": ssub.get("commit_sha", ""),
                        "detail": "subtask with commit_sha removed from plan",
                    })

            # Parent task marker/tag change (after subtask accounting).
            _classify_marker_change(
                result, _loc(pnum, t_idx), pnum,
                (pnum, t_idx, None), stask, e_marker, e_name)

    # State nodes the plan never touched (deleted tasks / whole phases).
    for pnum, phase_map in maps.items():
        for tk, (t_idx, stask) in phase_map["tasks"].items():
            if (pnum, t_idx, None) not in touched_state_keys:
                if stask.get("commit_sha") or _has_sha_in_subtasks(stask):
                    result["unmatched"].append({
                        "loc": _loc(pnum, t_idx), "phase": pnum,
                        "state_path": (pnum, t_idx, None),
                        "kind": "dropped_task_with_sha",
                        "name": stask.get("name", ""),
                        "commit_sha": stask.get("commit_sha", ""),
                        "detail": "task with commit_sha removed from plan",
                    })

    # SHA-liveness: flag any terminal node whose commit_sha is unreachable.
    if liveness:
        for p_idx, phase in enumerate(state.get("phases", []), start=1):
            for t_idx, task in enumerate(phase.get("tasks", []), start=1):
                _probe_liveness(track_dir, result, p_idx, t_idx, None, task)
                for s_idx, sub in enumerate(task.get("subtasks", []), start=1):
                    _probe_liveness(track_dir, result, p_idx, t_idx, s_idx, sub)

    return result


def _plan_marker_map(plan_path):
    """Parse ``(phase_num, task_name_key) / (.., subtask_name_key) → status char``.

    ``parse_plan`` discards the checkbox char; this re-walks the raw plan to
    capture it so reconcile can detect status edits (e.g. ``[x]`` → ``[>]``).
    Returns nested dict ``{phase_num: {"tasks": {key: char}, "subs": {tkey: {skey: char}}}}``.
    """
    markers = {}
    text = Path(plan_path).read_text()
    phase = None
    cur_task_key = None
    for raw in text.splitlines():
        line = raw.rstrip()
        pm = re.match(r"^##\s+Phase\s+(\d+)\b", line)
        if pm:
            phase = int(pm.group(1))
            markers.setdefault(phase, {"tasks": {}, "subs": {}})
            cur_task_key = None
            continue
        tm = re.match(r"^(\s*)-\s+\[([ x~!>#\-d])\]\s+(.*)", line)
        if tm and phase is not None:
            indent, marker, rest = tm.group(1), tm.group(2), tm.group(3)
            name = _clean_trailing_markers(re.sub(r"<!--.*?-->", "", rest, flags=re.DOTALL).strip())
            key = _name_key(name)
            if indent:
                if cur_task_key is not None:
                    markers[phase]["subs"].setdefault(cur_task_key, {})[key] = marker
            else:
                markers[phase]["tasks"][key] = marker
                cur_task_key = key
    return markers


def _classify_marker_change(result, loc, phase, state_path, node,
                            edited_marker, edited_name):
    """Bucket a node whose name matches: unchanged vs tag_or_status.

    Tags don't need storing (re-derived at dispatch from the name), so a *pure*
    tag change shows up as a name-string difference with the same status — still
    bucketed as ``tag_or_status`` so the new name is persisted. SHA is preserved
    whenever the edited marker is terminal.
    """
    cur_status = node.get("status", "pending")
    cur_name = node.get("name", "")
    new_status = edited_marker if edited_marker is not None else cur_status
    # name_key matched (that's why we're here), so a tag/wording edit shows up as
    # an exact-string difference — e.g. "Upgrade X" vs "[Docs] Upgrade X". Detect
    # that so the new name (with its tag) is persisted.
    name_changed = cur_name != edited_name
    status_changed = new_status != cur_status
    if not status_changed and not name_changed:
        result["unchanged"].append({"loc": loc, "phase": phase, "state_path": state_path})
        return
    keep_sha = new_status in _SHA_STATUSES
    result["tag_or_status"].append({
        "loc": loc, "phase": phase, "state_path": state_path,
        "name": node.get("name", ""), "new_name": edited_name,
        "old_status": cur_status, "new_status": new_status,
        "commit_sha": node.get("commit_sha", ""),
        "keep_sha": keep_sha,
    })


def _probe_liveness(track_dir, result, p_idx, t_idx, s_idx, node):
    sha = node.get("commit_sha", "")
    if not sha or node.get("status") not in _SHA_STATUSES:
        return
    if _is_sha_live(track_dir, sha):
        return
    result["dangling_sha"].append({
        "loc": _loc(p_idx, t_idx, s_idx), "phase": p_idx,
        "state_path": (p_idx, t_idx, s_idx),
        "name": node.get("name", ""), "commit_sha": sha,
    })


def _has_sha_in_subtasks(task):
    return any(sub.get("commit_sha") for sub in task.get("subtasks", []))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _resolve_index_from_spec(spec_part, names_in_order):
    """Resolve a task/subtask identifier (1-based index or name) → 1-based index.

    ``names_in_order`` is the list of cleaned names at that level, in order, so a
    name resolves to its position. Returns ``None`` if unresolvable.
    """
    spec_part = spec_part.strip()
    try:
        idx = int(spec_part)
        return idx if idx >= 1 else None
    except ValueError:
        pass
    target_key = _name_key(spec_part)
    for i, name in enumerate(names_in_order, start=1):
        if _name_key(name) == target_key:
            return i
    return None


def _apply_reconciliation(track_dir, diff, drops, clear_dangling, state=None):
    """Mutate state in one transaction to realize ``diff``.

    Resolves the repeatable ``--drop`` / ``--clear-dangling`` flags against the
    loaded state's name order, validates each against the diff (a flag naming a
    node that isn't actually a conflict is an error, not a silent no-op), and
    applies bucket mutations. SHA preservation follows the keep-set discipline of
    ``_do_split``. ``--rename`` is resolved upstream (pre-diff alias map).
    Returns ``(ok, errors, warnings)``.

    ``state`` may be passed by a caller that already loaded it (e.g.
    ``cmd_reconcile_plan``) to avoid a redundant ``track-state.json`` re-read;
    if omitted, it is loaded here for standalone use.
    """
    errors = []

    # --- validate & build flag resolutions -------------------------------
    # --rename is resolved upstream as a pre-diff alias map; here we handle only
    # --drop and --clear-dangling.
    drop_paths = set()
    clear_paths = set()

    if state is None:
        state = load(track_dir)

    for phase, rest in drops:
        sub = None
        if "." in rest:
            task_s, sub_s = rest.split(".", 1)
            t_idx = _resolve_index_from_spec(
                task_s.strip(),
                [t.get("name", "") for t in state["phases"][phase - 1].get("tasks", [])])
            if t_idx is not None:
                subs = state["phases"][phase - 1]["tasks"][t_idx - 1].get("subtasks", [])
                sub = _resolve_index_from_spec(sub_s.strip(), [s.get("name", "") for s in subs])
        else:
            t_idx = _resolve_index_from_spec(
                rest.strip(),
                [t.get("name", "") for t in state["phases"][phase - 1].get("tasks", [])])
        if t_idx is None or (sub is None and "." in rest):
            errors.append(f"--drop target not found: '{phase}:{rest}'")
            continue
        drop_paths.add((phase, t_idx, sub))

    for phase, rest in clear_dangling:
        sub = None
        if "." in rest:
            task_s, sub_s = rest.split(".", 1)
            t_idx = _resolve_index_from_spec(
                task_s.strip(),
                [t.get("name", "") for t in state["phases"][phase - 1].get("tasks", [])])
            if t_idx is not None:
                subs = state["phases"][phase - 1]["tasks"][t_idx - 1].get("subtasks", [])
                sub = _resolve_index_from_spec(sub_s.strip(), [s.get("name", "") for s in subs])
        else:
            t_idx = _resolve_index_from_spec(
                rest.strip(),
                [t.get("name", "") for t in state["phases"][phase - 1].get("tasks", [])])
        if t_idx is None or (sub is None and "." in rest):
            errors.append(f"--clear-dangling target not found: '{phase}:{rest}'")
            continue
        clear_paths.add((phase, t_idx, sub))

    # --- validate flags actually resolve real conflicts -------------------
    unmatched_paths = {u["state_path"] for u in diff["unmatched"] if u["state_path"]}
    dangling_paths = {d["state_path"] for d in diff["dangling_sha"]}
    for path in drop_paths:
        if path not in unmatched_paths:
            errors.append(f"--drop target is not an unmatched node: {path}")
    for path in clear_paths:
        if path not in dangling_paths:
            errors.append(f"--clear-dangling target is not a dangling SHA: {path}")

    # Refuse only on unresolved UNMATCHED nodes (rename-vs-delete ambiguity).
    # Dangling SHAs are advisory, not blocking: a terminal marker the user chose
    # is respected even when its SHA is unreachable (the git-reset recovery case —
    # the work is gone but the user has decided the status); non-terminal edits
    # auto-clear the dangling SHA and requeue (obviously-correct recovery). We
    # never silently resurrect a dead SHA onto new work — auto-clear is the only
    # mutation a dangling SHA triggers without an explicit flag.
    warnings = []
    for u in diff["unmatched"]:
        if u["state_path"] and u["state_path"] not in drop_paths:
            errors.append(f"unmatched node unresolved (needs --drop/--rename): "
                          f"{u['loc']} {u.get('name', '')!r} — {u['detail']}")
    # Advisory dangling-SHA warnings for nodes NOT auto-cleared (unchanged or
    # edited-to-terminal). Non-terminal edits are auto-cleared silently below.
    # ``edited_status`` is reused in the apply phase — diff is immutable between
    # the two, so build it once here.
    edited_status = {item["state_path"]: item["new_status"]
                     for item in diff["tag_or_status"]}
    for d in diff["dangling_sha"]:
        path = d["state_path"]
        if path in clear_paths:
            continue  # explicit clear — handled below, no warning
        new_status = edited_status.get(path)
        if new_status is not None and new_status not in _SHA_STATUSES:
            continue  # edited to non-terminal → auto-clear (no warning)
        warnings.append(
            f"{d['loc']} {d.get('name', '')!r}: commit_sha {d['commit_sha']} is "
            f"unreachable (git reset past it?); kept as-is. "
            f"Use --clear-dangling to requeue if the work should be redone.")

    if errors:
        return False, errors, warnings

    # --- apply, in one transaction ---------------------------------------
    with transaction(track_dir) as st:
        # tag_or_status: set status + persist (possibly renamed) name; keep SHA.
        for item in diff["tag_or_status"]:
            node = _node_at(st, item["state_path"])
            if node is None:
                continue
            node["status"] = item["new_status"]
            if item.get("new_name"):
                node["name"] = item["new_name"]
            if not item["keep_sha"]:
                # moving to a non-terminal status: SHA no longer applies.
                node.pop("commit_sha", None)
                node.pop("completed_at", None)

        # split: append pending subtasks under the parent (mirrors _do_split).
        # Group by parent to preserve order & dedupe.
        for item in diff["split"]:
            parent = _node_at(st, item["state_path"])
            if parent is None:
                continue
            subs = parent.setdefault("subtasks", [])
            key = _name_key(item["new_subtask"])
            if not any(_name_key(s.get("name", "")) == key for s in subs):
                subs.append({"name": item["new_subtask"], "status": "pending"})

        # drops: remove the node from its parent container.
        for path in drop_paths:
            _remove_node(st, path)

        # clear-dangling + auto-clear. Auto-clear fires ONLY when the user
        # explicitly edited the node to a NON-terminal marker (the obvious
        # recovery: they saw the reset, marked it pending/in_progress, reconcile
        # requeues). Dangling SHAs on unchanged or terminal-edited nodes are
        # advisory (left alone, warned above) — never silently cleared.
        clear_all = set(clear_paths)
        for d in diff["dangling_sha"]:
            path = d["state_path"]
            if path in clear_paths:
                continue
            new_status = edited_status.get(path)
            if new_status is None:
                continue  # unchanged node — leave its SHA (advisory only)
            if new_status in _SHA_STATUSES:
                continue  # edited to terminal — respect the marker (advisory)
            clear_all.add(path)  # edited to non-terminal — auto-clear + requeue
        for path in clear_all:
            node = _node_at(st, path)
            if node is None:
                continue
            node.pop("commit_sha", None)
            node.pop("completed_at", None)
            node["status"] = "pending"

        st["updated_at"] = now_iso()

    return True, [], warnings


def _node_at(state, path):
    """Fetch the dict at ``(phase, task[, subtask])`` 1-based indices, or None."""
    if path is None:
        return None
    p, t, s = path
    try:
        task = state["phases"][p - 1]["tasks"][t - 1]
    except (IndexError, KeyError):
        return None
    if s is None:
        return task
    try:
        return task["subtasks"][s - 1]
    except (IndexError, KeyError):
        return None


def _remove_node(state, path):
    p, t, s = path
    try:
        task = state["phases"][p - 1]["tasks"][t - 1]
    except (IndexError, KeyError):
        return
    if s is None:
        # removing a task: drop it from the phase
        state["phases"][p - 1]["tasks"].pop(t - 1)
    else:
        try:
            task["subtasks"].pop(s - 1)
        except (IndexError, KeyError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------

def cmd_reconcile_plan(track_dir, apply=False, force=False,
                       renames=None, drops=None, clear_dangling=None):
    """``track-state reconcile-plan`` backing.

    Dry-run by default: prints the bucketed diff and resolution hints, writes
    nothing. With ``--apply`` (and all conflicts resolved via flags), performs
    the mutations in one transaction, runs ``_do_sync_plan`` once, and makes one
    bookkeeping commit — the same staging set every conductor commit uses.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.is_file():
        out(dict(error=f"plan.md not found in {track_dir}"))
        return

    state = load(track_dir)
    edited = parse_plan(plan_path)
    if edited.get("errors"):
        out(dict(error="plan.md has parse errors; fix before reconciling",
                 plan_errors=edited["errors"]))
        return

    # Merge the line-keyed edited markers into each plan task so status edits are
    # detected. parse_plan discards the char; _plan_marker_map recovers it.
    markers = _plan_marker_map(plan_path)
    _stitch_markers(edited, markers)

    # Resolve --rename into an alias map BEFORE the diff so a renamed task matches
    # its pre-rename state node (and the new name is persisted via tag_or_status).
    # Spec form: "<phase>:<old>=<new>" → plan task named <new> aliases state node
    # named <old>. Resolve <old> against state to validate it exists.
    aliases = {}
    rename_errors = []
    parsed_renames = _parse_resolution(renames)
    for phase, rest in parsed_renames:
        if "=" not in rest:
            rename_errors.append(f"bad --rename (expected '<phase>:<old>=<new>'): "
                                 f"'{phase}:{rest}'")
            continue
        old, new = rest.split("=", 1)
        old, new = old.strip(), new.strip()
        try:
            state_tasks = state["phases"][phase - 1].get("tasks", [])
        except (IndexError, KeyError):
            rename_errors.append(f"--rename phase {phase} not found in state")
            continue
        old_names = [t.get("name", "") for t in state_tasks]
        if _resolve_index_from_spec(old, old_names) is None:
            rename_errors.append(f"--rename old name not found in phase {phase}: {old!r}")
            continue
        aliases.setdefault(phase, {})[_name_key(new)] = _name_key(old)
    if rename_errors:
        out(dict(error="invalid --rename specs", conflicts=rename_errors))
        return

    diff = _compute_reconciliation(track_dir, state, edited, aliases=aliases)

    if not apply:
        out(dict(dry_run=True, **_summarize(diff)))
        return

    ok, errors, warnings = _apply_reconciliation(
        track_dir, diff,
        _parse_resolution(drops), _parse_resolution(clear_dangling), state=state)
    if not ok:
        out(dict(error="reconcile refused — resolve the following and re-run",
                 conflicts=errors))
        return

    # Re-render plan.md from the now-correct state (safe direction), then commit.
    _do_sync_plan(track_dir)
    _git_commit_ensured(
        track_dir, "chore(conductor): Reconcile plan edits (name-keyed sync)")
    summary = _summarize(diff)
    summary["warnings"] = warnings
    out(dict(ok=True, **summary))


def _stitch_markers(edited_plan, markers):
    """Attach each parsed task/subtask's edited checkbox status to its dict.

    ``parse_plan`` discards the checkbox char; this walks the parsed tasks and
    sets ``_edited_status`` (a status name) on each from the line-keyed marker
    map so ``_compute_reconciliation`` can detect marker edits. Subtasks are
    promoted from bare strings to dicts carrying ``name`` + ``_edited_status``.
    """
    for ephase in edited_plan.get("phases", []):
        pnum = ephase["number"]
        ph_markers = markers.get(pnum, {"tasks": {}, "subs": {}})
        for task in ephase.get("tasks", []):
            tk = _name_key(task.get("name", ""))
            char = ph_markers.get("tasks", {}).get(tk)
            task["_edited_status"] = (
                _MARKER_TO_STATUS.get(char) if char else None)
            stitched_subs = []
            for sname in task.get("subtasks", []):
                sdict = {"name": sname} if isinstance(sname, str) else dict(sname)
                schar = ph_markers.get("subs", {}).get(tk, {}).get(_name_key(sname))
                sdict["_edited_status"] = (
                    _MARKER_TO_STATUS.get(schar) if schar else None)
                stitched_subs.append(sdict)
            task["subtasks"] = stitched_subs


def _summarize(diff):
    """Compact, human-readable envelope for the diff (printed by dry-run + apply)."""
    return {
        "unchanged": [u["loc"] for u in diff["unchanged"]],
        "tag_or_status": [
            {"loc": i["loc"], "name": i["name"], "new_name": i.get("new_name"),
             "old_status": i["old_status"], "new_status": i["new_status"],
             "commit_sha": i["commit_sha"], "keep_sha": i["keep_sha"]}
            for i in diff["tag_or_status"]],
        "split": [{"loc": s["loc"], "parent": s["parent_name"],
                   "new_subtask": s["new_subtask"]} for s in diff["split"]],
        "unmatched": [{"loc": u["loc"], "name": u.get("name", ""),
                       "kind": u["kind"], "detail": u["detail"],
                       "commit_sha": u.get("commit_sha")} for u in diff["unmatched"]],
        "dangling_sha": [{"loc": d["loc"], "name": d["name"],
                          "commit_sha": d["commit_sha"]} for d in diff["dangling_sha"]],
        "hints": _hints(diff),
    }


def _hints(diff):
    """Resolution instructions printed alongside the dry-run diff."""
    h = []
    for u in diff["unmatched"]:
        if u.get("state_path"):
            h.append(f"--drop \"{u['phase']}:{_drop_arg(u)}\"   # {u['detail']}")
        else:
            h.append(f"# {u['loc']} {u.get('name','')!r}: {u['detail']}")
    for d in diff["dangling_sha"]:
        h.append(f"--clear-dangling \"{d['phase']}:{_drop_arg(d)}\"   # "
                 f"git reset past {d['commit_sha']}?")
    return h


def _drop_arg(item):
    """Render a node's --drop/--clear-dangling argument as ``<task>`` or ``<task>.<subtask>``."""
    # state_path is (phase, task_idx[, subtask_idx]); index form is always valid.
    p, t, s = item["state_path"]
    return f"{t}" + (f".{s}" if s else "")
