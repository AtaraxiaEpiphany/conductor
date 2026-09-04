"""Per-dispatch workflow manifest — the code-composed seam between the
dispatch lifecycle and the executor's workflow (conductor/design/dispatch-manifest.md D4).

The dispatch envelope tells the executor WHO it is (TRACK_DIR/PHASE/TASK/...);
this manifest tells it WHAT it owes, resolved once in code at dispatch time
instead of re-derived in the agent's head from injected fragments:

- the resolved gate set (workflow-shape registry) + tag exemptions,
- the ONE workflow path decision — fast-path | bespoke docfile | inline
  prose | default TDD — with the docfile pinned by RELATIVE home,
- pointers to the docfile and gate semantics.

Contract:

- **Pure compose.** :func:`compose_manifest` is a deterministic function of
  ``(state, pre)`` — no timestamps, no absolute plugin paths in the body. A
  re-dispatch of the same state renders byte-identical bytes (the retry-
  determinism the dispatch envelope already guarantees; the manifest must not
  break it). Attempt budget deliberately lives in the ENVELOPE (ATTEMPT/
  MAX_RETRIES), not here — one home per fact.
- **Fail-open like the rest of dispatch.** Any registry/lookup miss degrades
  to the default-TDD decision rather than raising: dispatch must never
  deadlock over a malformed plan.md or registry (mirrors
  ``dispatch.resolve_phase_gate``).
- **Transient.** The file is single-homed as ``DISPATCH_MANIFEST_MARKER`` in
  lib/constants (gitignored via quality.py's derived tuple), written by
  ``prepare_dispatch`` (serial rail — execute action only; explorers owe no
  workflow) and ``prepare_wave`` (per worktree member, beside the wave
  marker), and reaped at dispatch-finalize / recover / wave teardown.
- **Floor, not ceiling.** The injected ``[Conductor Registry]`` block stays
  the deterministic floor (on-subagent-start recomputes the same profile);
  the manifest is the per-dispatch resolution. The golden test in
  tests/test_dispatch_manifest.py asserts the two agree.

Wave note: a member manifest is composed against the MAIN track state (the
worktree's committed track-state.json predates the member locks) and written
into the member's worktree track dir — the only state the member's executor
sees. Worktree teardown removes it with the worktree; no separate reap.
"""

from __future__ import annotations

from pathlib import Path

from lib.atomic_io import atomic_write_text
from lib.constants import DISPATCH_MANIFEST_MARKER
from .task_profiles import (
    DEFAULT_WORKFLOW_DOC,
    _plugin_root,
    _project_root,
    derive_task_tag,
    is_coverage_exempt,
    is_tdd_exempt,
    resolve_workflow_doc,
    strip_dispatch_tags,
    workflow_doc_for,
    workflow_for,
)


def manifest_path(track_dir) -> Path:
    """Path of the per-dispatch manifest under a track's ``.conductor/``."""
    return Path(track_dir) / ".conductor" / DISPATCH_MANIFEST_MARKER


def _rel_home(resolved: Path) -> tuple[str, str]:
    """(home, relative_path) for a resolved docfile — relative identity only.

    ``("project", "conductor/workflow/steps/x.md")`` when the winner sits under
    the project root, else ``("plugin", "templates/workflow/steps/x.md")``.
    Relative so the manifest body never bakes in an absolute plugin/project
    path (byte-stability across installs/upgrades).
    """
    proj = _project_root()
    if proj is not None:
        try:
            return "project", str(resolved.relative_to(proj))
        except ValueError:
            pass
    try:
        return "plugin", str(resolved.relative_to(_plugin_root()))
    except ValueError:
        return "plugin", str(Path("templates/workflow/steps") / resolved.name)


def _path_decision(state, pre):
    """The ONE workflow resolution this dispatch owes: ``(kind, detail)``.

    kind ∈ ``fast-path`` (both-exempt tag → Step 8 only), ``docfile`` (follow
    the named steps-library docfile), ``inline`` (legacy small-overlay
    ``workflow`` prose — fetch via registry-doc). Precedence mirrors
    task-executor §4.0: a declared ``workflow_doc`` wins, then inline prose,
    then the exemption, then default TDD. Fail-open: any miss falls through
    to ``("docfile", DEFAULT_WORKFLOW_DOC)``.

    Fast-path keys on BOTH exemptions (gates ⊆ [checkpoint]), not tdd alone:
    a tdd-only-exempt executor tag still owes the 80% coverage floor, and a
    fast-path executor cannot see the gate it fails. The baseline escapes
    the old looser check only because its one tdd-only-exempt tag (Explore)
    routes to explorer — a project overlay tag exposed the trap.
    """
    leading = _leading_tag(pre)
    try:
        if leading:
            doc = workflow_doc_for(leading)
            if doc:
                return "docfile", doc
            if workflow_for(leading):
                return "inline", leading
            if is_tdd_exempt([leading]) and is_coverage_exempt([leading]):
                return "fast-path", None
    except Exception:
        pass
    return "docfile", DEFAULT_WORKFLOW_DOC


def _leading_tag(pre):
    """The dispatch's leading tag (bracket-stripped), or ``None``."""
    tags = pre.get("tags") or []
    return tags[0] if tags else None


def compose_manifest(track_dir, state, pre) -> str:
    """Render the manifest body (pure; byte-identical for identical inputs)."""
    from .workflow_shapes import ac_grounding_for, gates_for, resolve_shape

    loc = f"P{pre.get('phase', '?')}.T{pre.get('task', '?')}"
    si = pre.get("subtask")
    if si is not None:
        loc += f".S{si}"
    tags = pre.get("tags") or []
    leading = _leading_tag(pre)

    try:
        shape = resolve_shape(state.get("workflow_shape"))
        gates = list(gates_for(shape))
        grounding = ac_grounding_for(shape)
    except Exception:  # fail-open: never block dispatch over the registry
        shape, gates, grounding = "default", ["tdd", "coverage", "checkpoint"], "test"

    kind, detail = _path_decision(state, pre)
    lines = [
        "# Dispatch Manifest (code-composed — do not edit)",
        "",
        "Written by `track-state` at dispatch time; reaped at dispatch-finalize.",
        "The injected `[Conductor Registry]` block is the deterministic floor —",
        "this manifest is the same resolution, pinned for THIS dispatch.",
        "",
        "## Task",
        f"- loc: {loc}",
        f"- name: {pre.get('name', '?')}",
        f"- tags: {', '.join(tags) or '(none)'} (leading: {leading or '(none)'})",
    ]
    # R3 misroute advisory (task-type ownership): an untagged task whose
    # description carries explore signals routes to task-executor — surface
    # the possible misroute at dispatch time, the earliest checkpoint for
    # in-flight plans (the init lint catches new ones). Pure + fail-open:
    # matcher errors never block dispatch. Explore-only by decision; other
    # tag families' false positives were not worth the nag.
    try:
        if (not leading and derive_task_tag(
                strip_dispatch_tags(pre.get("name", ""))) == "Explore"):
            lines.append(
                "- advisory: explore signals hit but task untagged — routes to "
                "task-executor; if the deliverable is findings (not code), "
                "task-executor will self-report MISROUTE and the verdict "
                "re-tags it")
    except Exception:  # noqa: BLE001 — advisory only, never fatal
        pass
    lines += [
        "",
        "## Resolved gates (workflow-shape: " + shape + ")",
        f"- gates: {', '.join(gates) or '(none)'}",
        f"- ac_grounding: {grounding}",
        f"- tdd_exempt: {str(bool(is_tdd_exempt(tags))).lower()}",
        f"- coverage_exempt: {str(bool(is_coverage_exempt(tags))).lower()}",
        "- fire rule: a gate fires iff listed above AND the tag is not exempt",
        "",
        "## Workflow path",
    ]
    if kind == "fast-path":
        lines += [
            "- path: fast-path (tdd-exempt tag → Step 8 commit only)",
            "- read Step 8 of ${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/"
            f"{DEFAULT_WORKFLOW_DOC} for the commit-message format (a project "
            "override at conductor/workflow/steps/ wins); skip Steps 3-7",
        ]
    elif kind == "inline":
        lines += [
            f"- path: inline (legacy `workflow` prose on tag [{detail}])",
            f"- fetch with one Bash call: `track-state registry-doc --tag {detail}`",
            "- follow that prose verbatim instead of default TDD",
        ]
    else:
        # resolve_workflow_doc takes the TAG (it re-reads the declared
        # workflow_doc + walks project ⊕ plugin homes); `detail` alone is a
        # bare filename with no home ladder. An untagged default decision has
        # no tag to resolve — pin the plugin default path directly.
        try:
            resolved = (resolve_workflow_doc(leading) if leading
                        else _plugin_root() / "templates" / "workflow"
                        / "steps" / DEFAULT_WORKFLOW_DOC)
            home, rel = _rel_home(resolved)
        except Exception:
            resolved = None
            home, rel = "plugin", str(
                Path("templates/workflow/steps") / DEFAULT_WORKFLOW_DOC)
        # Readable form of the docfile: the project home is already valid
        # project-relative; the plugin home gets the runtime-resolvable
        # ${CLAUDE_PLUGIN_ROOT} token (NOT an absolute install path —
        # byte-stability across installs/upgrades is preserved).
        read_path = rel if home == "project" else f"${{CLAUDE_PLUGIN_ROOT}}/{rel}"
        if resolved is not None and resolved.name != detail:
            # resolve fail-opened (the declared docfile exists in no steps
            # dir) — surface the FALLBACK honestly rather than pointing the
            # executor at a file that doesn't match the decision.
            lines += [
                f"- path: docfile `{detail}` NOT FOUND in any steps dir — "
                f"falling back to `{resolved.name}`; read {read_path} and "
                f"report the missing `{detail}` as a SPEC_DEVIATION",
            ]
        elif home == "project":
            lines += [
                f"- path: docfile `{detail}` — read {rel} (project home wins)",
            ]
        else:
            lines += [
                f"- path: docfile `{detail}` — read {read_path}"
                f"{' — Steps 3-8' if detail == DEFAULT_WORKFLOW_DOC else ''}"
                "; a project override at conductor/workflow/steps/ wins at read time",
            ]
    lines += [
        "",
        "## Pointers",
        "- this manifest's absolute path is the WORKFLOW_FILE line in your envelope",
        "- docfile bodies: ${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/ (plugin)",
        "  or conductor/workflow/steps/ (project wins)",
        "- gate semantics: ${CLAUDE_PLUGIN_ROOT}/runtime/core-contract.md",
        "- orchestrator-owned steps: ${CLAUDE_PLUGIN_ROOT}/templates/task-workflow.md",
        "",
    ]
    return "\n".join(lines)


def write_manifest(track_dir, state, pre) -> None:
    """Atomically (re)write the manifest for this dispatch. Deterministic —
    a retry overwrite renders the same bytes, so re-dispatch is idempotent."""
    path = manifest_path(track_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, compose_manifest(track_dir, state, pre))


def reap_manifest(track_dir) -> None:
    """Remove the manifest (dispatch-finalize / recover / teardown)."""
    manifest_path(track_dir).unlink(missing_ok=True)
