"""Workflow-studio DATA LAYER — read-with-origins, validated save, track binding.

The shape-studio server (:mod:`shape_studio`) and the ``registry-json`` /
``registry-save`` CLI subcommands both talk to the registries through THIS module
so there is one definition of "read for editing" and one definition of "write
safely" (non-negotiable #2: never duplicated in two languages / two paths).

The two registries (:mod:`workflow_shapes` and :mod:`task_profiles`) are
**fail-open on read, hard-error on write**: a malformed row silently degrades to
``default`` at dispatch (``resolve_shape`` / ``_profile``), which is correct for a
running conductor but wrong for an *edit* — the editor must REJECT a bad row
before it is written. :mod:`registry_validate` is that strict gate; this module
is the I/O wrapper around it.

Three things this layer owns that the raw registry modules do not:

1. **Origins.** Reads baseline + overlay as *separate files* (direct reads, NOT
   the ``lru_cache``-d ``_load()``, so always fresh and source-attributed) and
   tags every shape/tag with where it came from — the editor's B/O badges and
   the "which file am I editing?" intent both derive from this. The cached
   ``_load()`` flattens the two sources into one resolved dict and loses the
   attribution; an editor cannot edit what it cannot attribute.
2. **Validated write.** ``save_registry`` validates the new fragment, then
   validates the *merged result the conductor would resolve after the save*
   (the "a ``default`` must survive" invariant), preserves the ``_comment``/
   ``_fields`` doc blocks the editor must round-trip, copies existing→``.bak``,
   atomic-writes, and clears both modules' ``_load`` caches so the next read is
   fresh (the conductor re-reads per CLI call; only a long-lived *server*
   process holds the stale cache).
3. **Track binding** (``set_workflow_shape``, re-exported from :mod:`quality`)
   so the server binds a track to a shape in-process — the on-demand live
   preview the grill locked (set a shape → see its resolved graph).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from lib.atomic_io import atomic_write_json

from . import agent_roster as ar
from . import probes as pr
from . import registry_validate as rv
from . import task_profiles as tp
from . import workflow_shapes as ws
from .helpers import out  # noqa: F401 — re-exported; the CLI cmd_* wrappers emit via this

# Per-registry metadata: the data module, the overlay filename, the top-level
# data key ("shapes" vs "tags"), and the two validation entry points. The
# closed vocabularies themselves live in :mod:`registry_validate` (single
# source); this table only wires a registry to its module + validators.
#
# ``read_only`` registries (agent-roster, probes) render in the studio as
# VIEWERS: reads flow through the same origins machinery (B/O badges, merged
# view), but ``save_registry`` rejects writes — their sanctioned mutation
# paths are their own CLIs/overlay conventions, not the registry editor
# (roster add is a validated generator; the probes overlay is lint-gated).
# They carry no validator keys: the read_only gate fires before validation,
# and dead keys are a drift surface.
#
# ``default_block: False`` marks registries with no top-level ``default``
# object (the shapes/task-types fail-open fallback target) — the baseline
# structural read must not demand one.
_REGISTRIES = {
    "shapes": {
        "module": ws,
        "file": "workflow-shapes.json",
        "data_key": "shapes",
        "validate_fragment": rv.validate_shapes,
        "validate_merged": rv.validate_merged_shapes,
    },
    "task-types": {
        "module": tp,
        "file": "task-type-profiles.json",
        "data_key": "tags",
        "validate_fragment": rv.validate_task_types,
        "validate_merged": rv.validate_merged_task_types,
    },
    "agent-roster": {
        "module": ar,
        "file": "agent-roster.json",
        "data_key": "agents",
        "read_only": True,
        "default_block": False,
        "mutate_hint": "`track-state roster add` (the validated generator)",
    },
    "probes": {
        "module": pr,
        "file": "probes.json",
        "data_key": "probes",
        "read_only": True,
        "default_block": False,
        "mutate_hint": "extend the project overlay "
                       "<project>/conductor/workflow/probes.json — "
                       "`track-state check` surfaces overlay lint",
    },
}


def normalize_which(which):
    """Canonicalize a registry selector to a canonical registry name.

    Accepts the common aliases (the file stem, the data key, a singular form)
    so the CLI flag and the API query param can be lenient about which spelling
    a human types. Returns ``None`` for an unrecognized selector (callers raise
    / error on that rather than guessing — editing the wrong registry silently
    is worse than a clear "unknown --which").
    """
    if which is None:
        return None
    w = str(which).strip().lower()
    if w in ("shapes", "shape", "workflow-shapes", "workflow_shapes", "workflowshape"):
        return "shapes"
    if w in ("task-types", "task_types", "tags", "tag",
             "task-type-profiles", "task_type_profiles"):
        return "task-types"
    if w in ("agent-roster", "agent_roster", "roster", "agents", "agent"):
        return "agent-roster"
    if w in ("probes", "probe"):
        return "probes"
    return None


def _resolve_project_root(project_dir, module):
    """The project tree to overlay from.

    An explicit ``project_dir`` (the studio's ``--project-dir`` flag) is taken
    verbatim — the studio may point at a project that has a workflow overlay but
    no ``conductor/tracks/`` yet, so the module's cwd gate would wrongly refuse
    it. With no override, defer to the module's ``_project_root()`` ladder
    (``$CLAUDE_PROJECT_DIR`` → cwd-with-tracks-dir), the same ladder every
    overlay-aware registry agrees on.
    """
    if project_dir:
        return Path(project_dir).resolve()
    return module._project_root()


def _read_json(path):
    """Read a JSON object from ``path``; ``{}`` on missing/unparseable.

    Never raises — reads are best-effort (a missing overlay is the normal "no
    overlay" case; an unparseable one is surfaced via stderr and treated as
    absent so the editor still shows the baseline). Only dicts are returned;
    a non-object file is ignored.
    """
    if path is None or not Path(path).exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        print(f"WARNING: {path} is not a JSON object; ignoring.", file=sys.stderr)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: {path} unreadable ({exc}); ignoring.", file=sys.stderr)
    return {}


def _read_baseline(module, data_key, default_required=True):
    """Read the plugin baseline FILE, fail-open to the module's ``_FALLBACK``.

    Mirrors ``module._load_baseline`` but returns the raw file content for
    display rather than the resolved fallback silently — except a
    missing/unparseable baseline genuinely is the fallback case (the shipped
    file is the regression floor; if it is gone, the emergency mirror IS the
    baseline). The structural check matches ``_load_baseline``'s
    "must have a dict ``data_key``" gate; ``default_required`` adds the
    "and a dict ``default``" half ONLY for registries that carry a fallback
    target block (shapes/task-types — the read-only registries have no
    ``default`` row to demand).
    """
    path = module._plugin_registry_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(data, dict)
                    and isinstance(data.get(data_key), dict)
                    and (not default_required
                         or isinstance(data.get("default"), dict))):
                return data
            print(f"WARNING: baseline {path} has invalid shape; using fallback.",
                  file=sys.stderr)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: baseline {path} unreadable ({exc}); using fallback.",
              file=sys.stderr)
    return module._FALLBACK


def _overlay_path(which, project_dir):
    """The project overlay file for ``which``, or ``None`` when no project tree."""
    cfg = _REGISTRIES[which]
    root = _resolve_project_root(project_dir, cfg["module"])
    if root is None:
        return None
    return root / "conductor" / "workflow" / cfg["file"]


def _merge(baseline_doc, overlay_doc, data_key):
    """Shallow-merge overlay onto baseline — verbatim ``_merge_overlay`` semantics.

    ``{**baseline_default, **overlay_default}`` per-key; overlay entries added
    and win conflicts. Lifted here (not an import of ``_merge_overlay``) because
    those modules read the overlay from the *resolved project root* — the studio
    needs to merge an explicit overlay doc (the one just edited) regardless of
    where it lives, and against an explicit baseline doc. The arithmetic is
    identical; keeping it local keeps the studio's two-source model explicit.
    """
    merged_entries = dict(baseline_doc.get(data_key, {}))
    overlay_entries = overlay_doc.get(data_key)
    if isinstance(overlay_entries, dict):
        merged_entries.update(overlay_entries)

    merged_default = dict(baseline_doc.get("default", {}))
    overlay_default = overlay_doc.get("default")
    if isinstance(overlay_default, dict):
        merged_default.update(overlay_default)

    return {"default": merged_default, data_key: merged_entries}


def _origins(baseline_doc, overlay_doc, data_key):
    """Attribute each key (+``"default"``) to ``"baseline"`` or ``"overlay"``.

    A key is ``"overlay"`` iff the overlay FILE declares it (either as an entry
    or as a ``default`` block); otherwise ``"baseline"``. This is the editor's
    source-of-truth for the B/O badge — it answers "if I edit this row, which
    file changes?", which is exactly "is it in the overlay file?".

    Note the shipped registry carries BOTH a top-level ``default`` block (the
    fail-open fallback for unknown shape names) AND a ``shapes.default`` entry
    (the shape resolved for shape-name "default") — same string, two storage
    sites. The top-level block is the load-bearing one (the fallback target
    every unknown shape resolves to), so ``origins["default"]`` is set LAST and
    authoritatively from the top-level block; an entry coincidentally named
    "default" does not override it.
    """
    overlay_keys = set((overlay_doc.get(data_key) or {}).keys())
    baseline_keys = set((baseline_doc.get(data_key) or {}).keys())

    origin = {}
    for k in baseline_keys | overlay_keys:
        origin[k] = "overlay" if k in overlay_keys else "baseline"
    # The top-level `default` BLOCK wins the "default" key (set last).
    origin["default"] = ("overlay" if isinstance(overlay_doc.get("default"), dict)
                         else "baseline")
    return origin


def load_with_origins(which, project_dir=None):
    """Read a registry as three views + per-key origin attribution.

    Returns ``{which, baseline, overlay, merged, origins}``:

    - ``baseline`` — the plugin baseline FILE (fail-open to ``_FALLBACK``).
    - ``overlay`` — the project overlay FILE (``{}`` when absent).
    - ``merged`` — baseline ⊕ overlay (what the conductor resolves).
    - ``origins`` — ``{key: "baseline"|"overlay"}`` for every entry + ``default``.

    Reads bypass the cached ``_load()`` so a long-lived server process always
    sees fresh disk state (the conductor's per-CLI-call freshness does not apply
    to a server that keeps the process alive).
    """
    which = normalize_which(which)
    if which is None:
        raise ValueError("unknown registry 'which' "
                         "(expected shapes|task-types|agent-roster|probes)")
    cfg = _REGISTRIES[which]
    data_key = cfg["data_key"]

    baseline_doc = _read_baseline(cfg["module"], data_key,
                                  default_required=cfg.get("default_block", True))
    overlay_doc = _read_json(_overlay_path(which, project_dir))

    return {
        "which": which,
        "baseline": baseline_doc,
        "overlay": overlay_doc,
        "merged": _merge(baseline_doc, overlay_doc, data_key),
        "origins": _origins(baseline_doc, overlay_doc, data_key),
    }


def _target_path(which, target, project_dir):
    """Resolve the file a save writes to. ``None`` for an overlay with no project."""
    if target == "baseline":
        return _REGISTRIES[which]["module"]._plugin_registry_path()
    if target == "overlay":
        return _overlay_path(which, project_dir)
    return None


def _cache_clear():
    """Clear both registry modules' ``_load`` caches so the next read is fresh.

    The conductor re-reads per CLI call (each is a fresh process), but the
    studio server is one process — without this, a save would not be visible to
    the server's own subsequent reads (or to accessors like ``verifiers_for``
    that the resolve endpoint calls). Idempotent.
    """
    for cfg in _REGISTRIES.values():
        try:
            cfg["module"]._load.cache_clear()
        except AttributeError:
            # A future module that drops the cache: nothing to clear.
            pass


def save_registry(which, target, doc, project_dir=None):
    """Validate + atomically write a registry fragment. The strict-write gate.

    ``target`` ∈ ``{"overlay", "baseline"}``. The write is rejected unless BOTH:

    1. the fragment itself is valid (:func:`validate_shapes` /
       :func:`validate_task_types` — present-only, so an overlay fragment
       without ``default`` is fine); AND
    2. the *merged result the conductor would resolve after this save* is valid
       (:func:`validate_merged_shapes` / :func:`validate_merged_task_types` —
       the "a ``default`` must survive" invariant). For an overlay save the
       merged result is baseline ⊕ new-fragment; for a baseline save it is
       new-fragment ⊕ existing-overlay.

    On acceptance: preserves ``_comment``/``_fields`` doc blocks the existing
    file carries (so an editor that does not round-trip them does not strip
    them), copies the existing file to ``.bak``, atomic-writes, and clears the
    read caches. Returns ``{ok, path, which, target}`` or ``{ok: False,
    errors: [...]}`` (writes nothing on rejection).
    """
    which = normalize_which(which)
    if which is None:
        return {"ok": False, "errors": ["unknown registry 'which' "
                                        "(expected shapes|task-types|agent-roster|probes)"]}
    cfg = _REGISTRIES[which]
    if cfg.get("read_only"):
        return {"ok": False, "errors": [
            f"{which} is read-only in the studio (viewer) — mutate it through "
            f"its sanctioned surface: {cfg.get('mutate_hint', 'its own CLI')}"]}
    data_key = cfg["data_key"]

    if target not in ("overlay", "baseline"):
        return {"ok": False, "errors": [f"unknown target {target!r} "
                                        "(expected overlay|baseline)"]}

    # Path resolution BEFORE validation so a missing project dir is a clear
    # error rather than a silent write to the wrong place.
    path = _target_path(which, target, project_dir)
    if path is None:
        return {"ok": False, "errors": [
            "no project dir resolved — cannot write an overlay. "
            "Pass --project-dir or run inside a project tree "
            "(one with a conductor/ dir)."]}

    if not isinstance(doc, dict):
        return {"ok": False, "errors": ["registry document must be a JSON object"]}

    # 1. The fragment must be valid on its own.
    errs = list(cfg["validate_fragment"](doc))
    if errs:
        return {"ok": False, "errors": errs}

    # 2. The merged result the conductor WOULD resolve must be valid too — an
    #    overlay that leaves no default, or a baseline edit that the existing
    #    overlay then shadows into invalidity, is caught here.
    if target == "overlay":
        baseline_doc = _read_baseline(cfg["module"], data_key)
        merged = _merge(baseline_doc, doc, data_key)
    else:
        overlay_doc = _read_json(_overlay_path(which, project_dir))
        merged = _merge(doc, overlay_doc, data_key)
    merged_errs = list(cfg["validate_merged"](merged))
    if merged_errs:
        return {"ok": False, "errors": [f"merged result invalid: {e}" for e in merged_errs]}

    # Preserve the doc blocks the editor must round-trip. If the existing file
    # carries _comment/_fields and the incoming fragment omits them, carry them
    # forward — a save must never silently strip documentation.
    existing = _read_json(path)
    doc_to_write = dict(doc)
    for doc_key in ("_comment", "_fields"):
        if doc_key not in doc_to_write and doc_key in existing:
            doc_to_write[doc_key] = existing[doc_key]

    # .bak of the existing file (best-effort; a missing file is the first-write
    # case and has nothing to back up).
    if Path(path).exists():
        bak = Path(path).parent / (Path(path).name + ".bak")
        try:
            shutil.copy2(path, bak)
        except OSError as exc:
            return {"ok": False, "errors": [f"could not back up {path} ({exc})"]}

    # Ensure the overlay's parent dir exists (the studio may write an overlay
    # into a project that has conductor/ but not conductor/workflow/ yet).
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    atomic_write_json(path, doc_to_write)
    _cache_clear()
    return {"ok": True, "path": str(path), "which": which, "target": target}


def list_tracks(project_dir=None):
    """All tracks in a project, light-weight — for the studio's binding bar.

    Walks ``<project>/conductor/tracks/*/track-state.json`` and returns
    ``[{track_id, status, workflow_shape, dir}]``. Reads only the three fields
    the binding bar needs (no full state load) so a project with many tracks
    stays cheap. Skips unreadable tracks rather than failing the whole list.
    """
    root = _resolve_project_root(project_dir, ws)
    if root is None:
        return []
    tracks_dir = root / "conductor" / "tracks"
    if not tracks_dir.is_dir():
        return []
    tracks = []
    for entry in sorted(tracks_dir.iterdir()):
        state_path = entry / "track-state.json"
        if not state_path.is_file():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        tracks.append({
            "track_id": state.get("track_id") or entry.name,
            "status": state.get("status"),
            "workflow_shape": state.get("workflow_shape", "default"),
            "dir": str(entry),
        })
    return tracks


def set_workflow_shape(track_dir, shape):
    """Bind a track to a workflow shape — the compute+save half of the CLI cmd.

    Re-exported (imported once from :mod:`quality`) so the studio server has a
    single data-layer import surface; the strict ``validate-against-vocab then
    mutate`` contract lives in one place. Hard-rejects an unknown shape (a
    deliberate *set* must not silently become a no-op, even though reads fail
    open to ``default``). Returns ``{ok, workflow_shape, previous}`` or
    ``{ok: False, error, hint}``.
    """
    from .quality import set_workflow_shape as _impl  # lazy: keep the data layer's import surface light
    return _impl(track_dir, shape)


# --- CLI wrappers --------------------------------------------------------------
# Thin emitters over the data-layer functions so the sanctioned-subcommand
# machinery + scripting use apply. The CLI dispatcher parses --which/--target/
# --project-dir and hands them in already-normalized (normalize_which is called
# inside the data layer regardless, so a stray alias is still accepted).


def cmd_registry_json(which=None, project_dir=None):
    """Emit the origins-tagged registry snapshot (baseline + overlay + merged)."""
    try:
        out(load_with_origins(which, project_dir))
    except ValueError as exc:
        out({"ok": False, "error": str(exc)})


def cmd_registry_save(which, target, project_dir=None):
    """Read a registry document from stdin, validate, and write it.

    The document arrives on stdin (not a flag) so a multi-line JSON blob is not
    mangled by shell quoting. ``which`` and ``target`` come from CLI flags.
    """
    raw = sys.stdin.read()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        out({"ok": False, "errors": [f"invalid JSON on stdin: {exc}"]})
        return
    out(save_registry(which, target, doc, project_dir))
