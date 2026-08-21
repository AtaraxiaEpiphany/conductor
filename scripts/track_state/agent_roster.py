"""Agent-roster registry — the dispatch-scaffold axis (the third registry).

:mod:`task_profiles` says what a task-type's node SAYS; :mod:`workflow_shapes`
says the node SEQUENCE; this module says what SCAFFOLD a dispatched agent
RECEIVES — the result-format fence (the SubagentStart reminder), the
registry-vocab injection, the retry context, the single-writer dedupe guard,
and the SubagentStop recovery contract. The six pre-registry hardcoded name
lists (``AGENT_REMINDERS`` / ``_REGISTRY_AGENTS`` / ``_RETRY_AGENTS`` in
``on-subagent-start.py``, ``_WRITE_AGENTS`` in ``on-dispatch-dedupe.py``,
``_RESULT_FILE_INSTRUCTIONS`` + ``STDOUT_BLOCK_AGENTS`` in
``on-subagent-stop.py``, ``RESULT_FILE_AGENT_TYPES`` in ``lib.recovery``) died
into this registry: the hooks read the accessors below, so a project agent
gets conductor's scaffold with one overlay row and zero plugin edits —
registry owns policy (what scaffold each agent gets); agent bodies own
behavior.

Resolves as **plugin baseline ⊕ project overlay**, exactly mirroring the two
registries. The baseline is ``templates/workflow/agent-roster.json``; a
project drops ``conductor/workflow/agent-roster.json`` to add a
project-specific agent or override a built-in one — opt-in by file presence,
project rows added, project wins a conflicting name. Loading is **fail-open**:
a missing/malformed baseline falls back to :data:`_FALLBACK` (the empty roster
— "no scaffold", the pre-registry behavior for unknown names) with a loud
stderr warning; a malformed overlay falls back to the baseline alone. A
dispatch hook must never crash over a registry.

**Membership is load-bearing; grammar is not here.** The result-block GRAMMAR
(``RESULT_BLOCK_PATTERN`` / ``RESULT_END_TAG``) stays in :mod:`lib.recovery` —
it describes what ANY result block looks like, plugin or project. This
registry owns WHO gets which scaffold: an agent absent from the merged roster
is *unrostered* — dispatchable (the harness resolves the three name homes) but
fail-open with no scaffold, exactly the pre-registry behavior for built-in
agents. ``track-state check`` is the declared-names lint (a roster row naming
an agent that is not live, or a shape verifier/nodes name not on the roster,
is a lint error); runtime never denies.

Row shape (see the ``_fields`` block in the JSON for the data-side docs):

- ``class`` — executor | verifier | reviewer | advisory. executor derives
  ``single_writer=true`` (the dedupe guard set); every other class false.
- ``fence`` — the exact result-format reminder BODY (the loader composes the
  ``"[Conductor] Result format: "`` lead; :func:`reminder_for` is the
  byte-identical reconstruction of the old ``AGENT_REMINDERS`` values).
- ``single_writer`` / ``registry_injection`` / ``retry`` — optional bool
  overrides, default false (``single_writer``'s default is class-derived).
- ``recovery`` — result-file | stdout-block | none (default none), paired
  with ``recovery_instruction`` when not none (the strict-write validator
  enforces the pairing).
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


# The lead every reminder carries. Single home here (pre-registry it was
# welded into each of the 23 AGENT_REMINDERS values); the roster stores only
# the per-agent fence BODY so a lead change is a one-line edit.
REMINDER_LEAD = "[Conductor] Result format: "

# --- fallback: the empty roster ----------------------------------------------
# DO NOT edit this to change a scaffold — edit the registry JSON instead. This
# exists ONLY so a missing/malformed registry never crashes a dispatch hook.
# The empty roster means "no scaffold" — every accessor degrades to the
# pre-registry unknown-name behavior (no reminder, no injection, no recovery).
_FALLBACK: dict = {"agents": {}}


def _plugin_root() -> Path:
    """Resolve the plugin root, preferring ``$CLAUDE_PLUGIN_ROOT`` when it matches
    the ``__file__``-derived root (same ground-truth-first discipline as
    ``workflow_shapes._plugin_root`` / ``lib.env.get_plugin_root``). This module
    is at ``<plugin>/scripts/track_state/agent_roster.py``.
    """
    file_root = Path(__file__).resolve().parent.parent.parent
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        env_resolved = Path(env_root).resolve()
        if env_resolved == file_root:
            return env_resolved
    return file_root


def _plugin_registry_path() -> Path:
    """The always-present plugin baseline roster: ``<plugin>/templates/workflow/
    agent-roster.json``.
    """
    return _plugin_root() / "templates" / "workflow" / "agent-roster.json"


def _project_root() -> Path | None:
    """Resolve the *project* root (NOT the plugin root), or ``None`` when not in
    a real project tree. The same ladder every overlay-aware registry uses
    (mirrors ``workflow_shapes._project_root`` so all three agree on what "the
    project" is).

    1. ``$CLAUDE_PROJECT_DIR`` (Claude Code's authoritative injection) when set;
    2. else the cwd, **but only if** ``$cwd/conductor/tracks/`` is a dir — the
       repo's "this is a real project, not the plugin repo" signal;
    3. else ``None`` (no project, no overlay).

    Inlined (not an import of ``lib.env``): this module is imported
    transitively by the standalone hook scripts, and ``lib.env`` resolution can
    raise — inlining keeps the fail-open boundary tight.
    """
    env_proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_proj:
        return Path(env_proj).resolve()
    cwd = Path.cwd()
    if (cwd / "conductor" / "tracks").is_dir():
        return cwd
    return None


def _project_override_path() -> Path | None:
    """The project overlay roster candidate, or ``None`` when there is no
    project tree to overlay from: ``<project>/conductor/workflow/agent-
    roster.json`` — opt-in by file presence (absent = plugin defaults, zero
    behavior change).
    """
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "agent-roster.json"


def _load_baseline() -> dict:
    """Load the plugin baseline roster, fail-open to :data:`_FALLBACK`.

    This is the always-present floor: if the shipped roster is missing,
    unparseable, or structurally wrong, the empty roster keeps every dispatch
    hook running (unknown-name behavior) rather than crashing.
    """
    cand = _plugin_registry_path()
    try:
        if cand.exists():
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data.get("agents"), dict):
                return data
            reason = "has invalid shape (missing 'agents')"
        else:
            reason = "is missing"
    except (OSError, json.JSONDecodeError) as exc:
        reason = f"is unreadable ({exc})"
    print(
        f"WARNING: agent-roster registry at {cand} {reason}; "
        f"using the empty fallback roster.",
        file=sys.stderr,
    )
    return _FALLBACK


def _merge_overlay(baseline: dict) -> dict:
    """Shallow-merge a project overlay onto the baseline, if present.

    ``agents``: project rows are added; the project wins a conflicting name
    (row-level replacement — the overlay row REPLACES the baseline row, no
    per-key merge, so a project row states its full contract). The return
    shape is identical to the baseline's, so every consumer is overlay-blind
    — this merge is the single chokepoint that flows everywhere.

    Fail-open to *baseline alone* on any overlay read/shape error (the
    baseline is valid; a malformed project file must NOT drag the roster down
    to :data:`_FALLBACK`).
    """
    overlay_path = _project_override_path()
    if overlay_path is None or not overlay_path.exists():
        return baseline
    try:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: project agent-roster overlay at {overlay_path} "
            f"unreadable ({exc}); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline
    if not isinstance(overlay, dict):
        print(
            f"WARNING: project agent-roster overlay at {overlay_path} has "
            f"invalid shape (not an object); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline

    merged_agents = dict(baseline.get("agents", {}))
    overlay_agents = overlay.get("agents")
    if isinstance(overlay_agents, dict):
        merged_agents.update(overlay_agents)

    return {"agents": merged_agents}


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved roster (plugin baseline ⊕ project overlay).

    The baseline always loads (fail-open to :data:`_FALLBACK`); the project
    overlay, if present at ``<project>/conductor/workflow/agent-roster.json``,
    merges on top (project wins conflicts). Cached so the merge runs once per
    process — SubagentStart/Stop fire per dispatch and must stay cheap.
    """
    baseline = _load_baseline()
    return _merge_overlay(baseline)


def _agents() -> dict:
    """The resolved agent→row map (malformed non-dict rows skipped)."""
    return {
        name: row for name, row in _load().get("agents", {}).items()
        if isinstance(row, dict)
    }


# --- public API ----------------------------------------------------------------


def merged_agent_names() -> tuple[str, ...]:
    """The closed vocabulary of rostered agent names, in registry order.

    Registry order = baseline insertion order with project overlay rows
    appended-in-place by ``dict.update`` — deterministic within a process.
    This is the drift-killer source: nothing else may hand-maintain the agent
    name list.
    """
    return tuple(_agents().keys())


def row_for(name: str) -> dict | None:
    """The resolved row for one agent, or ``None`` when unrostered/malformed."""
    row = _agents().get(name)
    return row if isinstance(row, dict) else None


def reminder_for(name: str) -> str | None:
    """The composed SubagentStart result-format reminder, or ``None``.

    ``REMINDER_LEAD + fence`` — the byte-identical reconstruction of the
    pre-registry ``AGENT_REMINDERS`` value (the equivalence tests pin it).
    ``None`` for an unrostered agent or a row with a missing/non-str fence
    (fail-open: no reminder, never a crash).
    """
    row = row_for(name)
    if row is None:
        return None
    fence = row.get("fence")
    if not isinstance(fence, str) or not fence:
        return None
    return REMINDER_LEAD + fence


def class_for(name: str) -> str:
    """The agent's role class, or ``""`` when unrostered/malformed."""
    row = row_for(name)
    if row is None:
        return ""
    cls = row.get("class")
    return cls if isinstance(cls, str) else ""


def is_single_writer(name: str) -> bool:
    """Whether the dedupe guard treats this agent as single-writer-critical.

    Derived: ``row["single_writer"]`` when the row states it explicitly, else
    ``class == "executor"``. Unrostered → ``False`` (fail-open: an unknown
    agent is never denied a dispatch).
    """
    row = row_for(name)
    if row is None:
        return False
    explicit = row.get("single_writer")
    if isinstance(explicit, bool):
        return explicit
    return row.get("class") == "executor"


def single_writers() -> tuple[str, ...]:
    """The single-writer dedupe guard set, in registry order.

    The reconstruction of the pre-registry ``_WRITE_AGENTS`` tuple
    (:func:`is_single_writer` per row).
    """
    return tuple(n for n in merged_agent_names() if is_single_writer(n))


def registry_agents() -> tuple[str, ...]:
    """The agents that receive the resolved ``[Conductor Registry]`` vocab block
    at dispatch (rows with ``registry_injection: true``). The reconstruction of
    the pre-registry ``_REGISTRY_AGENTS`` set.
    """
    return tuple(
        n for n in merged_agent_names()
        if _agents()[n].get("registry_injection") is True
    )


def retry_agents() -> tuple[str, ...]:
    """The agents whose re-dispatch carries the prior attempt's failure record
    (rows with ``retry: true``). The reconstruction of the pre-registry
    ``_RETRY_AGENTS`` set.
    """
    return tuple(
        n for n in merged_agent_names()
        if _agents()[n].get("retry") is True
    )


def recovery_kind_for(name: str) -> str:
    """The SubagentStop completion signal kind: ``result-file`` |
    ``stdout-block`` | ``none``. Unrostered/malformed → ``"none"`` (fail-open:
    the stop always lands, the pre-registry unknown-name behavior).
    """
    row = row_for(name)
    if row is None:
        return "none"
    kind = row.get("recovery", "none")
    return kind if kind in ("result-file", "stdout-block") else "none"


def result_file_agents() -> tuple[str, ...]:
    """The agents gated on a fresh ``.conductor/result.json``. The
    reconstruction of ``lib.recovery.RESULT_FILE_AGENT_TYPES`` (whose
    membership moves here; the grammar stays in lib).
    """
    return tuple(
        n for n in merged_agent_names() if recovery_kind_for(n) == "result-file"
    )


def stdout_block_agents() -> tuple[str, ...]:
    """The agents gated on the ``---END RESULT---`` close tag. The
    reconstruction of the pre-registry ``STDOUT_BLOCK_AGENTS`` keys.
    """
    return tuple(
        n for n in merged_agent_names()
        if recovery_kind_for(n) == "stdout-block"
    )


def recovery_instruction_for(name: str) -> str:
    """The recovery-turn instruction for an agent, or ``""`` when it has none.

    The reconstruction of the pre-registry ``_RESULT_FILE_INSTRUCTIONS`` /
    ``STDOUT_BLOCK_AGENTS`` values. ``""`` for unrostered agents and rows whose
    ``recovery_instruction`` is missing/not a str (fail-open: the recovery
    block still fires with the lead alone).
    """
    row = row_for(name)
    if row is None:
        return ""
    instr = row.get("recovery_instruction")
    return instr if isinstance(instr, str) and instr else ""
