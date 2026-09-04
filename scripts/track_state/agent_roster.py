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


def agent_file_names() -> tuple[str, ...]:
    """Names of agent-definition files live on this machine — the three homes.

    The harness resolves a dispatched name against the plugin's shipped
    ``agents/``, the project's ``.claude/agents/``, and the user's
    ``~/.claude/agents/``. ``track-state check``'s declared-names lint (D4)
    compares every registry-declared agent name (roster rows, shape
    ``verifiers``/``nodes``) against this set: a declared name with no file in
    any home is a dead name (a typo), not a dispatchable agent — loud at
    check, fail-open at runtime. Not cached: three cheap globs, and unlike the
    registry files these dirs can appear mid-session (a project adding its
    first ``.claude/agents/`` agent).
    """
    names: list[str] = []
    seen: set[str] = set()
    homes = [_plugin_root() / "agents"]
    project = _project_root()
    if project is not None:
        homes.append(project / ".claude" / "agents")
    homes.append(Path.home() / ".claude" / "agents")
    for d in homes:
        try:
            for md in sorted(d.glob("*.md")):
                if md.stem not in seen:
                    seen.add(md.stem)
                    names.append(md.stem)
        except OSError:
            continue  # an absent/unreadable home is not an error — just fewer names
    return tuple(names)


def wrapper_skill_for(name: str) -> str | None:
    """The skill a wrapper agent's frontmatter preloads, or ``None``.

    ``roster add`` writes the wrapper at ``<project>/.claude/agents/<name>.md``
    with ``skills: [<skill>]`` frontmatter — the preload that puts a skill's
    procedure up front in the dispatched context (procedure up front, fetch
    reference on demand). This reads it back for surfaces that show which skill
    wraps into which agent (the studio's roster legend). Project wrapper wins,
    then the plugin's shipped agents dir — the same home order
    :func:`agent_file_names` walks. Fail-open ``None`` (absent file, unreadable,
    no skills line): a wrapper-less roster row is the common case, not an error.
    """
    if (not name or "/" in name or "\\" in name or name != name.strip()
            or name.startswith(".")):
        return None
    homes = []
    project = _project_root()
    if project is not None:
        homes.append(project / ".claude" / "agents")
    homes.append(_plugin_root() / "agents")
    for d in homes:
        try:
            text = (d / f"{name}.md").read_text(encoding="utf-8",
                                                errors="replace")
        except OSError:
            continue
        # Frontmatter = the first --- fenced block; a file without one is not
        # a wrapper (a plain agent .md) — skip to the next home.
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        for line in parts[1].splitlines():
            stripped = line.strip()
            if stripped.startswith("skills:"):
                val = stripped[len("skills:"):].strip().strip("[]").strip()
                first = val.split(",")[0].strip().strip("'\"")
                return first or None
    return None


def canonical_name(name: str) -> str | None:
    """The roster key a dispatched agent name resolves to, or ``None``.

    Dispatch names arrive **plugin-namespaced** when the plugin is installed
    (``conductor:refuter`` — the harness's agent id; the incident record is
    extensibility-review-2026-08 §incident) while roster keys are bare —
    :func:`roster_add` validates names to letters/digits/-/_, so no roster key
    can contain ``:``. Before this normalizer, every name-keyed lookup and
    membership check silently no-opped for namespaced dispatches: no floor, no
    fence reminder, no registry-vocab block, no recovery — the fail-open
    posture hid a fully-dead scaffold layer in installed-plugin projects.

    Full name wins (a bare key, or a hypothetical namespaced key); the
    tail-after-last-``:`` matches only when the full name is unrostered;
    anything else is ``None`` (unrostered — dispatchable, no scaffold).
    """
    if not name:
        return None
    agents = _agents()
    if name in agents:
        return name
    if ":" in name:
        tail = name.rsplit(":", 1)[1]
        if tail in agents:
            return tail
    return None


def row_for(name: str) -> dict | None:
    """The resolved row for one agent, or ``None`` when unrostered/malformed.

    Namespace-aware: a ``conductor:refuter`` dispatch resolves the ``refuter``
    row via :func:`canonical_name` (fail-open unchanged — unknown names stay
    ``None``, never an error).
    """
    key = canonical_name(name)
    row = _agents().get(key) if key else None
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


# --- roster add: adopt an outside skill as a wrapper agent ----------------------
#
# The generator behind ``track-state roster add`` (front-doored by the
# adopt-skill skill). Writes the two files the design's D3 recipe names — the
# wrapper agent (.claude/agents/<name>.md, skills-frontmatter preload) and the
# project overlay row — so adopting a skill is one command, not a hand-edited
# pair that can drift (wrapper fence ≠ roster fence).

# The scaffold defaults mirror the task-executor row (templates/workflow/
# agent-roster.json): an adopted skill IS an executor. The recovery instruction
# drops task-executor's "(Section 6.0)" pin — the wrapper body has no numbered
# sections to point at.
_DEFAULT_FENCE = "---TASK RESULT--- ... ---END RESULT---"
_DEFAULT_RECOVERY_INSTRUCTION = (
    "IMMEDIATELY call track-state write-result and print the ---TASK RESULT--- "
    "block. Report FAILURE if you cannot complete."
)

_WRAPPER_TEMPLATE = """\
---
name: {name}
description: {description}
tools: Bash, Read, Edit, Write, Grep, Glob
model: sonnet
effort: high
maxTurns: 48
skills: [{skill}]
---

# {name} — conductor executor wrapping the `{skill}` skill

You are dispatched by the conductor as an **executor** for one task. The
`{skill}` skill is preloaded above (procedure up front); fetch its reference
material only when the procedure points to it (preload procedure, fetch
reference).

## Procedure

1. Read the dispatch envelope's context blocks (the task's spec/plan sections
   it names). Self-load anything else you need from files.
2. Do the task's work following the `{skill}` skill procedure. Follow the
   project's conventions; keep changes scoped to the task.
3. Report the result (below). Honest FAILURE beats fake SUCCESS — the
   orchestrator re-dispatches a failure with your summary as context.

## Result contract

On completion, write the result file, then print the fence:

```bash
track-state write-result "<track-dir>" --status success|failure \\
  --commit-sha <sha> --summary "<one line>"
```

```
---TASK RESULT---
STATUS: SUCCESS | FAILURE
COMMIT_SHA: <sha, or empty on failure>
SUMMARY: <one line>
---END RESULT---
```

The stop hook gates your exit on a fresh result file — never stop without
writing one and printing the fence.

## Hard boundaries

- The ONE sanctioned write is `track-state write-result` (result.json).
- NEVER edit plan.md, track-state.json, or conductor/tracks state — the
  orchestrator owns dispatch-finalize (state updates, plan sync, the
  bookkeeping commit).
- NEVER create commits, tags, or branches yourself.
"""


def roster_add(name, skill, description=None, agent_class=None, fence=None,
               recovery=None, recovery_instruction=None,
               retry=True, registry_injection=True, force=False,
               project_dir=None):
    """Generate the wrapper agent + overlay roster row that adopt a skill.

    The D3 recipe as one command: writes ``<project>/.claude/agents/<name>.md``
    (frontmatter preloads the skill; body is the conductor-facing procedure +
    result contract) and upserts the agent's row into
    ``<project>/conductor/workflow/agent-roster.json`` (row-level replace by
    name, ``_comment``/``_fields`` preserved, ``.bak`` + atomic write per the
    registry-studio precedent). Defaults mirror the task-executor scaffold;
    ``--class``/``--fence``/``--recovery``/``--recovery-instruction`` override,
    and ``--no-retry``/``--no-registry-injection`` opt out of the scaffold
    parity booleans (a persona bound as a task-class executor — the
    ``agent`` field on a tag row — wants BOTH on: it dispatches through the
    same retry + registry-injection machinery task-executor does).

    ``retry``/``registry_injection`` are written EXPLICITLY either way (the
    always-write-explicitly doctrine): at read time an absent ``registry_injection``
    means "no injection" and an absent ``retry`` means "no retry budget", so a
    generated row must never leave them to those defaults by accident.

    Validates the row (fragment + the baseline ⊕ new-overlay merge) BEFORE any
    write and returns ``{ok: False, errors: [...]}`` on a bad row, a name with
    path separators, an orphaned recovery instruction, or a wrapper file that
    exists (unless *force*). On success clears the roster read cache and
    returns ``{ok, agent_path, roster_path, lint: []}`` (``lint`` reserved for
    future post-write checks).
    """
    # Lazy imports keep the hook path clean: this generator is CLI-only, and
    # the dispatch hooks importing this module must not pay for (or depend on)
    # the validator or the atomic-write helper.
    from lib.atomic_io import atomic_write_json
    from .registry_validate import (
        AGENT_CLASSES, RECOVERY_KINDS, validate_agent_row, validate_merged_roster,
    )

    if not name or not skill:
        return {"ok": False, "errors": ["both <name> and --skill are required"]}
    if "/" in name or name != name.strip() or not name.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "errors": [
            f"invalid agent name {name!r} — letters/digits/-/_ only (it becomes "
            f"both a filename and a roster key)"]}
    if agent_class is not None and agent_class not in AGENT_CLASSES:
        return {"ok": False, "errors": [
            f"unknown --class {agent_class!r} (expected {list(AGENT_CLASSES)})"]}
    if recovery is not None and recovery not in RECOVERY_KINDS:
        return {"ok": False, "errors": [
            f"unknown --recovery {recovery!r} (expected {list(RECOVERY_KINDS)})"]}

    if project_dir is not None:
        root = Path(project_dir).resolve()
        if not root.is_dir():
            return {"ok": False, "errors": [
                f"project dir {project_dir!r} does not exist — refusing to "
                f"create conductor/ scaffolding in a typo'd path"]}
    else:
        root = _project_root()
    if root is None:
        return {"ok": False, "errors": [
            "no project dir resolved — pass --project-dir or run inside a "
            "project tree (one with conductor/tracks/)"]}

    kind = recovery or "result-file"
    if kind == "none" and recovery_instruction:
        return {"ok": False, "errors": [
            "--recovery-instruction set but recovery is none — an instruction "
            "without a recovery kind never fires; set --recovery or drop it"]}

    row = {"class": agent_class or "executor",
           "fence": fence or _DEFAULT_FENCE,
           # Scaffold parity with task-executor (the persona-binding target):
           # written EXPLICIT, never left to the read-time defaults.
           "registry_injection": bool(registry_injection),
           "retry": bool(retry)}
    if kind != "none":
        row["recovery"] = kind
        row["recovery_instruction"] = (recovery_instruction
                                       or _DEFAULT_RECOVERY_INSTRUCTION)

    # The overlay as it will exist AFTER this write (existing rows + doc
    # blocks preserved, this row replacing any same-name row), so validation
    # sees exactly what the conductor will resolve.
    overlay = {"agents": {}}
    roster_path = root / "conductor" / "workflow" / "agent-roster.json"
    if roster_path.exists():
        try:
            existing = json.loads(roster_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("agents"), dict):
                overlay = existing
        except (OSError, json.JSONDecodeError):
            return {"ok": False, "errors": [
                f"{roster_path} is unreadable — fix or remove it before adding "
                f"agents (the conductor currently fails open to the baseline)"]}
    overlay.setdefault("agents", {})[name] = row

    # Validate before touching disk: the row itself, then the merge the
    # conductor WOULD resolve (baseline ⊕ the post-write overlay).
    errs = list(validate_agent_row(name, row))
    merged = _merge_overlay(_load_baseline())
    merged.setdefault("agents", {}).update(overlay["agents"])
    errs.extend(validate_merged_roster(merged))
    if errs:
        return {"ok": False, "errors": errs}

    agent_path = root / ".claude" / "agents" / f"{name}.md"
    if agent_path.exists() and not force:
        return {"ok": False, "errors": [
            f"{agent_path} already exists — pass --force to overwrite"]}

    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(
        _WRAPPER_TEMPLATE.format(
            name=name, skill=skill,
            description=description
            or f"Conductor executor wrapping the {skill} skill."),
        encoding="utf-8")

    if roster_path.exists():
        try:
            import shutil
            shutil.copy2(roster_path, roster_path.parent / (roster_path.name + ".bak"))
        except OSError as exc:
            return {"ok": False, "errors": [f"could not back up {roster_path} ({exc})"]}
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(roster_path, overlay)

    _load.cache_clear()
    return {"ok": True, "agent_path": str(agent_path),
            "roster_path": str(roster_path), "lint": []}
