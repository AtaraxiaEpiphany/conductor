"""Shape Studio — a stdlib read/write visualizer for the workflow registries.

The "see and change the workflow" surface. A single-file web UI (stdlib
``http.server`` + vanilla JS + inline SVG/CSS; **zero** pip / npm / CDN deps —
the conductor ships no front-end toolchain and this is not the file that adds
one) that lets a human:

- **see** each registry (workflow-shapes + task-type-profiles) as baseline ⊕
  overlay, with every row badged by origin (B = plugin baseline, O = project
  overlay) and the resolved-workflow graph drawn live;
- **change** a row through validating dropdowns (the closed vocabularies are the
  single source in :mod:`registry_validate` — the dropdowns are data-driven from
  them, never re-typed), with a strict gate that rejects a bad edit BEFORE it is
  written (fail-open on read, hard-error on write — non-negotiable #3); and
- **bind** a track to a shape on demand (set shape → see its resolved graph) —
  the live-preview decision the grill locked.

Architecture: the server imports the data layer (:mod:`registry_studio`) and the
join (:func:`misc.build_view_envelope`) **in-process** — no subprocess, so the
validation the CLI uses is the exact validation a save goes through here
(non-negotiable #2: one definition, two entry points). The ``shape-studio`` /
``registry-json`` / ``registry-save`` CLI subcommands are thin wrappers over the
same data-layer functions, kept as first-class commands so the sanctioned-set
machinery applies.

Security: binds **127.0.0.1 only** (never 0.0.0.0 — the studio is a local dev
tool, not a network service), no auth. Saves never trust a client-supplied path
— ``save_registry`` resolves the target from ``which``/``target``/project-dir
internally. The one client-supplied filesystem reference (the track dir in the
shape-binding endpoint) is gated by :func:`_validate_track_dir` (must be a real
track-state.json-bearing dir under the project's ``conductor/tracks/`` tree).
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import registry_studio as rs
from . import registry_validate as rv
from . import task_profiles as tp
from . import workflow_shapes as ws
from .helpers import extract_tags, flag
from .misc import build_view_envelope


# --- vocab + graph helpers (pure; shared with the resolve endpoint) ------------

# Node legend — display prose ONLY (UI help text, never injected into a prompt).
# Each entry is grounded in the agent's canonical `agents/<name>.md` frontmatter;
# if an agent's role changes, update its .md first and keep these one-liners in
# rough sync. A second home for *display* semantics is low-drift (mirrors how
# `instruction`/`when_to_use` are display prose), so long as no dispatch path
# ever reads this — and none does. The spine/verifier ``kind`` is NOT stored
# here — it is derived from ``rv.SPINE_NODES``/``rv.VERIFIERS`` at the
# ``/api/nodes`` handler, so the partition has one source.
NODE_DOCS = {
    "spec-planner": {
        "role": "Decomposes the brief into an ordered task tree every other node works from.",
        "produces": "spec.md, plan.md",
    },
    "explorer": {
        "role": "Read-only codebase mapping before planning; records Layer-0 findings for the executor.",
        "produces": "Exploration Notes (handoff)",
    },
    "task-executor": {
        "role": "Implements one task via the TDD loop (red/green/refactor, Steps 3-8); self-loads all context.",
        "produces": "code + tests + result",
    },
    "phase-checker": {
        "role": "Checkpoint synthesizer — fans out the verifiers, owns fix-and-retry, stamps the checkpoint commit.",
        "produces": "checkpoint commit",
    },
    "ac-tracer": {
        "role": "AC-evidence trace — verifies each acceptance criterion is grounded (by tests or by a review anchor, per ac_grounding). Read-only.",
        "produces": "per-AC verdict",
    },
    "build-runner": {
        "role": "L0 compile/build/typecheck — resolves the project's build command and runs it ONCE, no fix, no edit (read-only). The cheapest-first floor: catches code the test suite never imports.",
        "produces": "pass / fail",
    },
    "test-runner": {
        "role": "L1 verify-only — resolves the test command and runs it ONCE, no fix, no edit (read-only).",
        "produces": "pass / fail",
    },
}

# Per-field "what does editing this actually do?" — the honest signal that stops
# the "edit nodes, nothing changes" trap. `class` ∈ drives | intent | display,
# mapped to the frontend's green/amber/muted badge language so a user can
# predict an edit's effect BEFORE reading the text. Shape fields first:
_SHAPE_FIELD_EFFECTS = {
    "verifiers": ("drives", "The checkpoint fans out exactly these verifiers (ac-tracer → build-runner → test-runner, cheapest-first). Drop a tier here and it will not run at the checkpoint — but a test-grounded shape (ac_grounding != review) MUST keep build-runner, or the studio's save gate rejects it (dropping the compile tier reopens the unimported-module hole)."),
    "gates": ("drives", "These track-level quality gates fire, composed with each task's per-tag exemptions. Drop tdd/coverage for a non-code shape."),
    "checkpoint_policy": ("drives", "Whether the checkpoint phase actually RUNS. run (default) fans it out; skip-if-declared short-circuits it — but ONLY with a declared integrity substitute (ac_grounding=review), else the studio's save gate rejects it (a skip without a substitute breaks the AC-verification guarantee)."),
    "nodes": ("intent", "Declares the intended spine topology. ADVISORY: dispatch order is fixed (planner→executor→checker); this records intent and surfaces a shape_violation when reality drifts. It does NOT reorder execution."),
    "verify_policy": ("display", "Whether a checkpoint phase runs at all (checkpoint vs none). Read by registry-doc; not injected into any prompt."),
    "stop_condition": ("display", "What marks the shape done. Display-only today."),
    "ac_grounding": ("drives", "How acceptance criteria are grounded: test (the default — spec-integrity measures AC→test coverage) or review (a non-code deliverable — spec-integrity measures AC→anchor + review attestation). LOAD-BEARING: it switches the grounding scan AND is the declared substitute that lets a shape drop the build/test tiers (a review shape owes no compile; a test shape owes the build tier)."),
    "instruction": ("display", "Human/tooling reference prose. NOT injected into the orchestrator prompt (contrast the task-type `workflow` field, which IS)."),
    "when_to_use": ("display", "Human/tooling reference prose. NOT injected into the orchestrator prompt."),
}
# Task-type fields: most of these DO drive behavior (routing, exemptions,
# injected workflow prose) — the honesty story here is "nearly everything
# matters except the hint," the inverse of the shapes story.
_TAG_FIELD_EFFECTS = {
    "workflow": ("drives", "Injected into task-executor as the per-task workflow prose; replaces default TDD when present (e.g. [Migrate]'s preservation loop)."),
    "route": ("drives", "Determines the dispatch category: manual (deferred) | explore (explorer) | executor (task-executor)."),
    "tdd_exempt": ("drives", "Per-task exemption from the TDD (red/green/refactor) gate, composed with the shape's gates."),
    "coverage_exempt": ("drives", "Per-task exemption from the coverage (F2/F3) gate, composed with the shape's gates."),
    "refactor": ("drives", "Opts the task into one tactical-refactorer pass after it succeeds."),
    "auto_propose": ("drives", "If false, derive_task_tag NEVER goal-detects this tag — it is opt-in (authored on the name) only."),
    "over_tag_risk": ("drives", "Flags the tag as an over-tagging risk so the advisory classifier guards against false positives."),
    "signals": ("drives", "Keyword list derive_task_tag signal-matches to advisorially classify a tagless description."),
    "when_to_use": ("display", "Injected into spec-planner as the tag's one-line hint. Display elsewhere."),
}


def _effects(table):
    """Shape an effects table into the {field: {class, text}} the frontend renders."""
    return {f: {"class": cls, "text": txt} for f, (cls, txt) in table.items()}


def _vocab():
    """The closed vocabularies + per-registry field schema, for the frontend.

    The single source is :mod:`registry_validate`; this shapes it into the
    "which fields does an editor row carry, and what may each hold?" metadata the
    frontend's dynamic form generator consumes — so adding a field or a vocab
    member is a one-place edit and the dropdowns can never drift from the
    write-time validator (a second hand-maintained vocab table is exactly the
    Bucket-B drift liability the prose-style contract warns about).
    """
    return {
        "shapes": {
            "list_fields": {
                "nodes": list(rv.SPINE_NODES),
                "verifiers": list(rv.VERIFIERS),
                "gates": list(rv.GATES),
            },
            "scalar_fields": {
                "verify_policy": list(rv.VERIFY_POLICIES),
                "stop_condition": list(rv.STOP_CONDITIONS),
                "ac_grounding": list(rv.AC_GROUNDINGS),
                "checkpoint_policy": list(rv.CHECKPOINT_POLICIES),
            },
            "text_fields": ["instruction", "when_to_use"],
            # LOAD-BEARING (drives dispatch) vs ADVISORY (records intent only) is
            # derived from _SHAPE_FIELD_EFFECTS so the badge, the field guide, and
            # these lists share one taxonomy and can't drift apart.
            "load_bearing": [f for f, (cls, _) in _SHAPE_FIELD_EFFECTS.items() if cls == "drives"],
            "advisory": [f for f, (cls, _) in _SHAPE_FIELD_EFFECTS.items() if cls == "intent"],
            # Per-field "what does editing this do?" with a drives|intent|display
            # class — the honest signal. Single source; the Field Guide + each
            # field's inline badge both read this (never re-typed in the frontend).
            "effects": _effects(_SHAPE_FIELD_EFFECTS),
        },
        "task-types": {
            "scalar_fields": {"route": list(rv.ROUTES)},
            "bool_fields": ["tdd_exempt", "coverage_exempt", "refactor",
                            "auto_propose", "over_tag_risk"],
            "text_fields": ["when_to_use", "workflow"],
            "list_fields": {"signals": None},  # free-form keyword strings
            "effects": _effects(_TAG_FIELD_EFFECTS),
        },
    }


def _shape_graph(shape):
    """The resolved-workflow graph for a shape name (accessors fail-open)."""
    return {
        "shape": shape,
        "nodes": list(ws.nodes_for(shape)),
        "verifiers": list(ws.verifiers_for(shape)),
        "gates": list(ws.gates_for(shape)),
        "verify_policy": ws.verify_policy_for(shape),
        "stop_condition": ws.stop_condition_for(shape),
        "ac_grounding": ws.ac_grounding_for(shape),
        "checkpoint_policy": ws.checkpoint_policy_for(shape),
    }


def _task_profile(tag):
    """One tag's resolved profile for the studio's per-task drill-down.

    Fail-soft by design: an unknown / typo / removed-by-overlay tag still resolves
    (via :func:`task_profiles._profile` → the ``default`` profile) so the
    drill-down never 500s. ``known`` tells the frontend whether the tag is in the
    live registry vocab (so it can badge an authored-but-removed or mistyped tag
    rather than silently rendering ``default`` as if it were the real answer).
    """
    known = bool(tag) and tag in tp.TAG_VOCAB()
    prof = tp._profile(tag) if tag else {}  # noqa: SLF001 — registry-internal profile lookup
    return {
        "tag": tag,
        "known": known,
        "route": prof.get("route", "executor"),
        "tdd_exempt": bool(prof.get("tdd_exempt", False)),
        "coverage_exempt": bool(prof.get("coverage_exempt", False)),
        "refactor": bool(prof.get("refactor", False)),
        "auto_propose": bool(prof.get("auto_propose", True)),
        "over_tag_risk": bool(prof.get("over_tag_risk", False)),
        "when_to_use": prof.get("when_to_use", ""),
        "workflow": prof.get("workflow", ""),
    }


def _task_card(phase_index, unit, parent_task=None):
    """One per-task drill-down card from a ``task_tree`` unit.

    The leading tag is derived from the LIVE name (:func:`extract_tags` — what
    dispatch reads), with a fallback to the unit's cached ``task_type`` when the
    name carries no bracket tag (e.g. a name edited after plan time). The card
    carries the tag's resolved profile inline so the frontend renders a task's
    workflow + exemptions without a second round-trip.
    """
    name = unit.get("name") or ""
    tags = extract_tags(name)
    tag = tags[0] if tags else None
    if tag is None:
        cached = unit.get("task_type")
        if cached and cached != "default":
            # ``task_type`` is the lowercased leading tag; resolve it against the
            # live (capitalized) registry keys case-insensitively — the same
            # approach the dispatch path uses, and it survives multi-capital tags
            # (.capitalize() would mangle e.g. [OAuth2] → Oauth2 → unresolved).
            low = str(cached).lower()
            tag = next((k for k in tp.TAG_VOCAB() if k.lower() == low), None)
    prof = _task_profile(tag)
    is_subtask = parent_task is not None
    return {
        "phase": phase_index,
        "task": parent_task if is_subtask else unit.get("index"),
        "subtask": unit.get("index") if is_subtask else None,
        "name": name,
        "status": unit.get("status", "pending"),
        "commit_sha": unit.get("commit_sha"),
        "tag": tag,
        "coverage_pct": unit.get("coverage_pct"),
        "known": prof["known"],
        "route": prof["route"],
        "workflow": prof["workflow"],
        "when_to_use": prof["when_to_use"],
        "tdd_exempt": prof["tdd_exempt"],
        "coverage_exempt": prof["coverage_exempt"],
        "refactor": prof["refactor"],
    }


def _task_cards(env):
    """Per-task drill-down cards for the studio's whole-track map.

    Walks the envelope's ``task_tree`` (phase → task → subtask) and emits one card
    per task and per subtask, so the frontend can render the whole track as a tree
    where every node shows its leading tag, workflow prose, and exemptions. The
    envelope itself is left untouched — this is a studio-namespaced enrichment the
    dashboard/status renderers never read (the ONE-join invariant holds).
    """
    cards = []
    for phase in env.get("task_tree") or []:
        pi = phase.get("index")
        for tk in phase.get("tasks") or []:
            cards.append(_task_card(pi, tk))
            for sub in tk.get("subtasks") or []:
                cards.append(_task_card(pi, sub, parent_task=tk.get("index")))
    return cards


def _validate_track_dir(track_dir, project_dir):
    """Security gate for client-supplied track paths. Returns a resolved Path or None.

    The shape-binding endpoint is the ONE place a client names a filesystem path.
    It must clear three checks before it reaches :func:`load`/:func:`save`:

    1. **No traversal / absolute escape.** Resolved plainly (no ``..`` games).
    2. **It is actually a track** (a ``track-state.json`` sibling exists).
    3. **It is under the project's ``conductor/tracks/`` tree** when a project
       dir is known — so a local studio cannot be pointed at, say, the plugin's
       own source via a crafted ``track_dir``. "Known" includes auto-detection:
       when no ``--project-dir`` is given, the project root is resolved via the
       same ``workflow_shapes._project_root`` ladder the registry ops use, so
       containment holds in the common auto-detect case (not just the explicit
       one). Fail-open ONLY when no project is detectable at all.

    Returns ``None`` on any failure; callers emit a 400. No client path is ever
    passed to the state layer without clearing this.
    """
    if not track_dir or not isinstance(track_dir, str):
        return None
    raw = Path(track_dir)
    # Reject traversal outright (a track name never needs to escape upward).
    if ".." in raw.parts:
        return None
    try:
        resolved = raw.resolve(strict=False)
    except (OSError, ValueError):
        return None
    if not (resolved / "track-state.json").is_file():
        return None
    # When no explicit --project-dir was given, resolve one via the SAME ladder
    # the registry ops use (workflow_shapes._project_root: $CLAUDE_PROJECT_DIR →
    # cwd-with-tracks-dir), so containment holds in auto-detect mode too. Without
    # this, a studio started with no --project-dir would accept ANY
    # track-state.json-bearing dir on the host — the containment the docstring
    # promises was enforced only for the explicit-dir case, leaving the auto-
    # detect case (the common one) open. If the ladder detects no project
    # (None), there is nothing to contain against and we fail OPEN — but a real
    # track always lives under a detectable project, so this only relaxes for
    # genuinely project-less hosts.
    if not project_dir:
        project_dir = ws._project_root()
    if project_dir:
        tracks_root = Path(project_dir).resolve(strict=False) / "conductor" / "tracks"
        try:
            resolved.relative_to(tracks_root)
        except ValueError:
            return None
    return resolved


# --- HTTP handler --------------------------------------------------------------

class _StudioState:
    """Per-server mutable config the handler closes over (project-dir binding).

    ``BaseHTTPRequestHandler`` has no server-handle on the handler instance, so
    the project dir is stashed on a tiny shared object the handler reads through
    ``self.server.studio``. (A closure over a module global would also work but
    couples the handler to a single global server; this keeps it per-instance so
    a test can spin up an isolated server with its own temp project.)
    """

    def __init__(self, project_dir):
        self.project_dir = project_dir


def _json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler, body, status=200):
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _read_body(handler):
    """Read + JSON-decode a request body of declared Content-Length. Returns
    ``(parsed_or_None, error_or_None)``."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None, "invalid Content-Length"
    if length <= 0 or length > 1_000_000:  # 1 MiB ceiling: a registry doc is small.
        return None, "empty or oversize body"
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"invalid JSON body: {exc}"


class _Handler(http.server.BaseHTTPRequestHandler):
    # Trim the default per-request noise (the studio logs to the console; a
    # request line per asset would drown the save/save-validate signal).
    def log_message(self, fmt, *args):  # noqa: A003 — BaseHTTPRequestHandler API
        sys.stderr.write(f"[studio] {fmt % args}\n")

    # The server instance carries the project-dir binding.
    @property
    def _project_dir(self):
        return self.server.studio.project_dir

    # --- GET ------------------------------------------------------------------
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path == "/" or path == "/index.html":
            _html_response(self, _PAGE)
            return
        if path == "/api/registry":
            which = rs.normalize_which(qs.get("which"))
            if which is None:
                _json_response(self, {"ok": False, "error": "bad ?which="}, 400)
                return
            try:
                snap = rs.load_with_origins(which, self._project_dir)
            except (ValueError, OSError) as exc:
                _json_response(self, {"ok": False, "error": str(exc)}, 400)
                return
            snap["vocab"] = _vocab()[which]
            _json_response(self, snap)
            return
        if path == "/api/resolve":
            shape = qs.get("shape")
            track = qs.get("track")
            if shape is not None:
                _json_response(self, _shape_graph(shape))
                return
            if track is not None:
                resolved = _validate_track_dir(track, self._project_dir)
                if resolved is None:
                    _json_response(self, {"ok": False,
                                          "error": "invalid or unresolvable track"}, 400)
                    return
                try:
                    env = build_view_envelope(str(resolved))
                except (OSError, KeyError, ValueError) as exc:
                    _json_response(self, {"ok": False, "error": str(exc)}, 400)
                    return
                # Studio-only enrichment the dashboard/status renderers ignore
                # (the ONE-join invariant holds — build_view_envelope is
                # untouched; this is a namespaced add-on key).
                env["studio"] = {"task_cards": _task_cards(env)}
                _json_response(self, env)
                return
            _json_response(self, {"ok": False, "error": "need ?shape= or ?track="}, 400)
            return
        if path == "/api/tracks":
            _json_response(self, {"tracks": rs.list_tracks(self._project_dir)})
            return
        if path == "/api/nodes":
            # The node/verifier legend — display prose grounded in agents/*.md.
            # ``kind`` (spine vs verifier) is derived from the registry's single
            # source (rv.SPINE_NODES/VERIFIERS), not hand-duplicated per entry.
            nodes = {n: {"kind": "spine" if n in rv.SPINE_NODES else "verifier", **doc}
                     for n, doc in NODE_DOCS.items()}
            _json_response(self, {"nodes": nodes})
            return
        if path == "/api/task-profile":
            # One tag's resolved profile, fail-soft on an unknown/blank tag.
            _json_response(self, _task_profile(qs.get("tag") or ""))
            return
        if path == "/api/vocab":
            _json_response(self, _vocab())
            return
        if path == "/api/state":
            # The CLI's --baseline flag surfaces here (the only channel from a
            # per-invocation CLI flag to the statically-served SPA).
            _json_response(self, dict(_PAGE_STATE))
            return

        _json_response(self, {"ok": False, "error": f"not found: {path}"}, 404)

    # --- POST -----------------------------------------------------------------
    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/registry/save":
            body, err = _read_body(self)
            if err:
                _json_response(self, {"ok": False, "error": err}, 400)
                return
            if not isinstance(body, dict):
                _json_response(self, {"ok": False, "error": "body must be an object"}, 400)
                return
            which = rs.normalize_which(body.get("which"))
            target = body.get("target")
            doc = body.get("doc")
            if which is None:
                _json_response(self, {"ok": False,
                                      "error": "bad/missing 'which'"}, 400)
                return
            if target not in ("overlay", "baseline"):
                _json_response(self, {"ok": False,
                                      "error": "bad/missing 'target' (overlay|baseline)"}, 400)
                return
            if not isinstance(doc, dict):
                _json_response(self, {"ok": False,
                                      "error": "bad/missing 'doc'"}, 400)
                return
            # save_registry resolves the path INTERNALLY from which/target/
            # project-dir — no client path is trusted. Hard-rejects invalid.
            # A filesystem error (read-only install, full disk, perms) escapes
            # save_registry unguarded (its .bak is try/except'd but the mkdir /
            # atomic_write_json raises are not) — catch it here so the handler
            # returns a JSON error + no stray .bak is left half-written.
            try:
                result = rs.save_registry(which, target, doc, self._project_dir)
            except (OSError, ValueError) as exc:
                result = {"ok": False, "error": f"save failed: {exc}"}
            _json_response(self, result, 200 if result.get("ok") else 400)
            return

        if path == "/api/track/shape":
            body, err = _read_body(self)
            if err:
                _json_response(self, {"ok": False, "error": err}, 400)
                return
            if not isinstance(body, dict):
                _json_response(self, {"ok": False, "error": "body must be an object"}, 400)
                return
            resolved = _validate_track_dir(body.get("track_dir"), self._project_dir)
            shape = body.get("shape")
            if resolved is None:
                _json_response(self, {"ok": False,
                                      "error": "invalid track_dir"}, 400)
                return
            if not isinstance(shape, str) or not shape:
                _json_response(self, {"ok": False,
                                      "error": "bad/missing 'shape'"}, 400)
                return
            try:
                result = rs.set_workflow_shape(str(resolved), shape)
            except (OSError, ValueError) as exc:
                result = {"ok": False, "error": f"set shape failed: {exc}"}
            _json_response(self, result, 200 if result.get("ok") else 400)
            return

        _json_response(self, {"ok": False, "error": f"not found: {path}"}, 404)


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded so a browser's parallel asset/API requests don't serialize.

    ``daemon_threads=True`` so a hung request thread never blocks process exit
    (the studio is Ctrl-C'd, not gracefully drained).
    """
    daemon_threads = True
    allow_reuse_address = True


def serve(port=0, host="127.0.0.1", project_dir=None, open_browser=True):
    """Start the studio server (blocking). Returns the bound (host, port).

    ``port=0`` lets the OS pick a free port (printed for the user to click).
    ``host`` defaults to loopback ONLY — the studio is a local dev tool, never a
    network service, so binding 0.0.0.0 would expose the write endpoint to the
    LAN with no auth. ``project_dir`` pins which project's overlay/tracks the
    studio reads and writes; None falls back to the registry modules' own
    project-root ladder.
    """
    httpd = _ThreadingServer((host, port), _Handler)
    httpd.studio = _StudioState(project_dir)
    bound_host, bound_port = httpd.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    # flush=True so the URL is visible immediately when stdout is redirected to
    # a file/pipe (Python block-buffers then; without the flush a launcher that
    # reads our output to open the browser would hang waiting for a line that is
    # stuck in the buffer).
    print(f"[studio] serving {url} (project={project_dir or '<auto>'})", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — headless / no DE: open() is best-effort
            print("[studio] (could not open a browser; open the URL above manually)",
                  flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[studio] shutting down.")
    finally:
        httpd.server_close()
    return (bound_host, bound_port)


def cmd_shape_studio(args):
    """CLI entry: ``track-state shape-studio [--port N] [--host H]
    [--project-dir DIR] [--baseline] [--theme dark|light|system] [--no-browser]``.

    ``--baseline`` is a convenience that flips the studio's DEFAULT write target
    hint to the plugin baseline (an advanced, ships-to-every-project edit); the
    frontend still offers the toggle. ``--theme`` sets the SPA's initial color
    scheme (``system`` follows the OS via ``prefers-color-scheme``). Both are
    FLAGS, not subcommands, so neither touches the four sanctioned-subcommand /
    drift-site registration points. Parses flags from the raw argv slice
    because shape-studio takes NO track-dir (its flags start at argv[2]).
    """
    port = flag(args, "--port")
    host = flag(args, "--host") or "127.0.0.1"
    project_dir = flag(args, "--project-dir")
    baseline_default = "--baseline" in args
    open_browser = "--no-browser" not in args
    try:
        port_val = int(port) if port else 0
    except ValueError:
        from .helpers import out
        out({"ok": False, "error": f"--port requires an integer, got {port!r}"})
        return
    # baseline_default + theme are surfaced to the frontend via /api/state (the
    # only channel from a per-invocation CLI flag to the statically-served SPA).
    _PAGE_STATE["default_target"] = "baseline" if baseline_default else "overlay"
    theme = flag(args, "--theme") or "system"
    if theme not in _THEMES:
        from .helpers import out
        out({"ok": False,
             "error": f"--theme must be one of {','.join(_THEMES)}, got {theme!r}"})
        return
    _PAGE_STATE["theme"] = theme
    serve(port=port_val, host=host, project_dir=project_dir, open_browser=open_browser)


# The served page reads this for the CLI's --baseline default and --theme. A
# module-level dict (not a constant) so cmd_shape_studio can set it per-invocation.
_THEMES = ("dark", "light", "system")
_PAGE_STATE = {"default_target": "overlay", "theme": "system"}


# --- the frontend (one HTML string; vanilla JS + inline SVG/CSS) ---------------
# Kept inline so the studio is genuinely one file with zero asset dependencies —
# no separate JS/CSS to serve, no build step, no CDN. The JS is data-driven from
# /api/vocab + /api/registry so both registries render through one code path.

_PAGE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conductor Workflow Studio</title>
<style>
  /* Two palettes via custom properties. `dark` is the root default; a
     `[data-theme="light"]` attribute (set by the theme toggle / --theme flag)
     flips every component at once — one attribute, whole UI recolored. `system`
     is resolved in JS to whichever the OS prefers. No framework, no CDN. */
  :root {
    --bg:#0b0f17; --panel:#10172a; --panel-2:#161f36; --elev:#1d2942;
    --bd:#273350; --bd-soft:#1f2a40; --fg:#e6edf3; --fg-dim:#a7b6d0;
    --muted:#6f7e9a; --acc:#4ea8ff; --acc-2:#22d3ee;
    --ok:#34d399; --warn:#fbbf24; --err:#f87171;
    --base:#8b98b5; --over:#e0b341; --glow:rgba(78,168,255,.55);
    --shadow:0 6px 24px rgba(0,0,0,.35);
    --grad:linear-gradient(135deg,var(--acc),var(--acc-2));
    --ok-tint:rgba(52,211,153,.16); --warn-tint:rgba(251,191,36,.18);
    --acc-tint:rgba(78,168,255,.16); --acc2-tint:rgba(34,211,238,.16);
    --err-tint:rgba(248,113,113,.14); --over-tint:rgba(224,179,65,.18);
    --bg-img:radial-gradient(1200px 600px at 85% -10%, rgba(34,211,238,.08), transparent 60%),
              radial-gradient(900px 500px at -10% 110%, rgba(78,168,255,.09), transparent 60%);
  }
  [data-theme="light"] {
    --bg:#eef2f9; --panel:#ffffff; --panel-2:#f5f8fc; --elev:#ffffff;
    --bd:#d4deec; --bd-soft:#e5ebf4; --fg:#16203a; --fg-dim:#3f4f6b;
    --muted:#7385a0; --acc:#1668d6; --acc-2:#0c8aa8;
    --ok:#15935c; --warn:#9a6700; --err:#cf222e;
    --base:#5b6a85; --over:#9a6700; --glow:rgba(22,104,214,.28);
    --shadow:0 6px 18px rgba(20,40,80,.12);
    --ok-tint:rgba(21,147,92,.14); --warn-tint:rgba(154,103,0,.14);
    --acc-tint:rgba(22,104,214,.12); --acc2-tint:rgba(12,138,168,.12);
    --err-tint:rgba(207,34,46,.10); --over-tint:rgba(154,103,0,.14);
    --bg-img:radial-gradient(1200px 600px at 85% -10%, rgba(12,138,168,.07), transparent 60%),
              radial-gradient(900px 500px at -10% 110%, rgba(22,104,214,.06), transparent 60%);
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         color:var(--fg); background:var(--bg); background-image:var(--bg-img); background-attachment:fixed; }
  header { display:flex; gap:14px; align-items:center; padding:11px 18px;
           background:linear-gradient(180deg,var(--panel),var(--panel-2)); border-bottom:1px solid var(--bd);
           box-shadow:var(--shadow); flex-wrap:wrap; position:relative; z-index:5; }
  header h1 { font-size:15px; margin:0; font-weight:700; letter-spacing:.01em;
              background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; }
  header .spacer { flex:1; }
  .seg { display:flex; gap:4px; background:var(--panel-2); border:1px solid var(--bd); border-radius:10px; padding:3px; }
  .seg button { background:transparent; border:0; color:var(--fg-dim); padding:6px 13px; border-radius:7px;
                cursor:pointer; font:inherit; font-weight:550; transition:.15s; }
  .seg button.active { background:var(--grad); color:#06121f; box-shadow:0 2px 10px var(--glow); }
  .seg button:not(.active):hover { color:var(--fg); background:var(--elev); }
  main { display:grid; grid-template-columns:282px 1fr 384px; height:calc(100vh - 60px); }
  .pane { overflow:auto; padding:14px; }
  .pane.left { border-right:1px solid var(--bd); background:linear-gradient(180deg,var(--panel),var(--bg)); }
  .pane.center { border-right:1px solid var(--bd); padding:16px 18px; }
  .pane.right { background:linear-gradient(180deg,var(--panel),var(--bg)); }
  h2 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:14px 0 8px; font-weight:650; }
  h2:first-child { margin-top:0; }
  .entries-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
  ul.entries { list-style:none; margin:0; padding:0; }
  ul.entries li { padding:8px 10px; border-radius:9px; cursor:pointer; display:flex; align-items:center; gap:9px;
                  transition:.12s; border:1px solid transparent; }
  ul.entries li:hover { background:var(--elev); }
  ul.entries li.selected { background:var(--acc-tint); border-color:var(--acc); }
  .name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge { font-size:10px; font-weight:700; padding:2px 6px; border-radius:7px; border:1px solid; line-height:1.4; }
  .badge.B { color:var(--base); border-color:var(--base); }
  .badge.O { color:var(--over); border-color:var(--over); }
  .fx { font-size:9.5px; font-weight:700; padding:1px 6px; border-radius:6px; text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }
  .fx.drives { color:var(--ok); background:var(--ok-tint); }
  .fx.intent { color:var(--warn); background:var(--warn-tint); }
  .fx.display { color:var(--muted); background:var(--elev); }
  .muted { color:var(--muted); font-size:12px; }
  .row { display:flex; gap:8px; align-items:center; margin:8px 0; flex-wrap:wrap; }
  label.fld { font-size:12px; color:var(--fg-dim); min-width:96px; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  input[type=text], select, textarea { font:inherit; padding:7px 9px; border:1px solid var(--bd);
          border-radius:8px; background:var(--panel-2); color:var(--fg); width:100%; transition:.12s; }
  input:focus, select:focus, textarea:focus { outline:0; border-color:var(--acc); box-shadow:0 0 0 3px var(--glow); }
  textarea { min-height:58px; resize:vertical; }
  .field { margin:9px 0; }
  .checks { display:flex; gap:10px; flex-wrap:wrap; }
  .checks label { min-width:0; display:flex; gap:5px; align-items:center; font-size:12px; color:var(--fg-dim); }
  select[multiple] { min-height:74px; }
  .btn { background:var(--grad); color:#06121f; border:0; border-radius:9px; padding:8px 14px;
         cursor:pointer; font:inherit; font-weight:650; transition:.15s; box-shadow:0 2px 10px var(--glow); }
  .btn:hover { filter:brightness(1.07); }
  .btn.ghost { background:var(--panel-2); color:var(--fg); border:1px solid var(--bd); box-shadow:none; }
  .btn.ghost:hover { border-color:var(--acc); }
  .btn:disabled { opacity:.45; cursor:not-allowed; filter:none; box-shadow:none; }
  .status { font-size:12px; padding:5px 9px; border-radius:7px; }
  .status.ok { color:var(--ok); } .status.err { color:var(--err); } .status.warn { color:var(--warn); }
  .card { background:var(--panel); border:1px solid var(--bd); border-radius:14px; padding:14px; margin-bottom:14px; box-shadow:var(--shadow); }
  .graph-wrap { background:linear-gradient(180deg,var(--panel-2),var(--bg)); border:1px solid var(--bd);
                border-radius:12px; padding:14px; overflow:hidden; }
  .pill { font-size:11px; padding:3px 8px; border-radius:11px; background:var(--elev); border:1px solid var(--bd); color:var(--fg-dim); }
  .pill.sm { padding:2px 7px; }
  .note { font-size:11px; color:var(--muted); font-style:italic; }
  details { background:var(--panel-2); border:1px solid var(--bd); border-radius:11px; padding:2px 12px; margin:8px 0; }
  details[open] { padding-bottom:8px; }
  summary { cursor:pointer; font-size:12px; color:var(--fg-dim); padding:8px 0; font-weight:550; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary::before { content:"\25B8 "; color:var(--acc); }
  details[open] summary::before { content:"\25BE "; }
  .node-grid { display:grid; gap:7px; }
  .node-row { display:flex; gap:9px; align-items:flex-start; padding:8px 10px; border-radius:9px; background:var(--elev); border:1px solid var(--bd-soft); }
  .node-row .nk { font-size:10px; font-weight:700; padding:2px 7px; border-radius:6px; flex-shrink:0; }
  .node-row .nk.spine { color:var(--acc); background:var(--acc-tint); }
  .node-row .nk.verifier { color:var(--over); background:var(--over-tint); }
  .node-row .nm { font-weight:600; font-size:12.5px; }
  .node-row .ds { font-size:11px; color:var(--fg-dim); }
  .node-row .pr { font-size:10px; color:var(--muted); margin-top:2px; }
  .fg-row { display:flex; gap:8px; align-items:flex-start; padding:6px 9px; border-radius:8px; margin:4px 0; font-size:11.5px; }
  .fg-row .fn { font-weight:650; min-width:92px; color:var(--fg); }
  .fg-row .ft { color:var(--fg-dim); flex:1; }
  .phase { margin:6px 0 12px; }
  .phase-h { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--acc); font-weight:650; margin-bottom:5px; }
  .tcard { display:flex; gap:10px; align-items:flex-start; padding:9px 11px; border-radius:10px; background:var(--panel-2);
           border:1px solid var(--bd); margin:5px 0; transition:.12s; position:relative; }
  .tcard.here { border-color:var(--ok); box-shadow:0 0 0 1px var(--ok), 0 4px 16px rgba(52,211,153,.18); }
  .tcard.here::before { content:""; position:absolute; left:-3px; top:8px; bottom:8px; width:3px; border-radius:3px;
                        background:var(--ok); animation:pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:.35;} 50%{opacity:1;} }
  .tcard .idx { font-size:10px; color:var(--muted); min-width:38px; padding-top:2px; }
  .tcard .body { flex:1; min-width:0; }
  .tcard .tn { font-weight:600; font-size:12.5px; word-break:break-word; }
  .tcard .meta { display:flex; gap:5px; flex-wrap:wrap; margin-top:5px; align-items:center; }
  .stat { font-size:10px; font-weight:700; padding:2px 7px; border-radius:6px; border:1px solid; }
  .stat.completed { color:var(--ok); border-color:var(--ok); background:var(--ok-tint); }
  .stat.in_progress { color:var(--warn); border-color:var(--warn); background:var(--warn-tint); }
  .stat.pending { color:var(--muted); border-color:var(--bd); }
  .stat.failed { color:var(--err); border-color:var(--err); background:var(--err-tint); }
  .stat.skipped, .stat.deferred { color:var(--base); border-color:var(--bd); }
  .tag-chip { font-size:10px; font-weight:700; padding:2px 7px; border-radius:6px; color:var(--acc-2);
              background:var(--acc2-tint); border:1px solid var(--acc-2); }
  .tag-chip.unknown { color:var(--err); background:var(--err-tint); border-color:var(--err); }
  .wf { font-size:11px; color:var(--fg-dim); margin-top:6px; padding:7px 9px; background:var(--bg); border-radius:7px; border-left:3px solid var(--acc-2); }
  .sub { margin-left:20px; }
  .recipe { font-size:12px; line-height:1.65; }
  .recipe .kb { font-weight:700; display:block; margin-bottom:4px; }
  .recipe div { margin:3px 0; }
  .drives { color:var(--ok); font-weight:650; }
  .intent { color:var(--warn); font-weight:650; }
  code { background:var(--elev); padding:1px 5px; border-radius:5px; font-size:11px; }
  .theme-toggle { display:flex; gap:3px; background:var(--panel-2); border:1px solid var(--bd); border-radius:9px; padding:3px; }
  .theme-toggle button { background:transparent; border:0; color:var(--fg-dim); width:30px; height:28px; border-radius:6px; cursor:pointer; font-size:13px; }
  .theme-toggle button.active { background:var(--elev); color:var(--acc); }
  /* SVG graph classes — fills/strokes read the theme vars so the graph recolors
     on theme toggle with no re-render (var() resolves in CSS, not in SVG
     presentation attributes strings, so we use classes). */
  .sg-spine { fill:var(--acc); fill-opacity:.15; stroke:var(--acc); stroke-width:1.5; }
  .sg-spine.glow { filter:url(#sgglow); }
  .sg-node-text { fill:var(--fg); font-weight:600; }
  .sg-conn { stroke:var(--acc); stroke-width:2; opacity:.65; fill:none; }
  .sg-arr { fill:var(--acc); }
  .sg-verif { fill:var(--over); fill-opacity:.13; stroke:var(--over); stroke-width:1.4; stroke-dasharray:5; }
  .sg-gate-on { fill:var(--ok); fill-opacity:.16; stroke:var(--ok); stroke-width:1.3; }
  .sg-gate-off { fill:var(--panel-2); stroke:var(--bd); stroke-width:1.3; }
  .sg-gate-on-txt { fill:var(--ok); font-weight:600; }
  .sg-gate-off-txt { fill:var(--muted); }
  .sg-label { fill:var(--muted); font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>&#x1F399;&#xFE0F; Conductor Workflow Studio</h1>
  <div class="seg">
    <button id="tab-shapes" class="active" onclick="switchTab('shapes')">Workflow Shapes</button>
    <button id="tab-task-types" onclick="switchTab('task-types')">Task Types</button>
  </div>
  <span class="spacer"></span>
  <label class="muted">Track
    <select id="track-select" onchange="onTrackChange()" style="width:auto;margin-left:6px">
      <option value="">(none)</option>
    </select>
  </label>
  <span id="track-shape-info" class="muted"></span>
  <div class="theme-toggle" title="Color theme">
    <button id="th-dark" onclick="applyTheme('dark')" title="Dark">&#x1F319;</button>
    <button id="th-light" onclick="applyTheme('light')" title="Light">&#x2600;&#xFE0F;</button>
    <button id="th-system" onclick="applyTheme('system')" title="System">&#x1F5A5;&#xFE0F;</button>
  </div>
</header>
<main>
  <div class="pane left">
    <div class="entries-head">
      <h2 style="margin:0" id="entries-title">Shapes</h2>
      <button class="btn ghost" onclick="addEntry()" style="padding:4px 10px;font-size:12px">+ new</button>
    </div>
    <input type="text" id="entry-name" placeholder="(select or add)" disabled>
    <ul class="entries" id="entries"></ul>
    <details>
      <summary>Origin key</summary>
      <div class="muted" style="padding:4px 2px"><span class="badge B">B</span> plugin baseline &nbsp; <span class="badge O">O</span> project overlay</div>
    </details>
    <details open id="field-guide">
      <summary>Field guide — what each edit does</summary>
      <div id="field-guide-body"></div>
    </details>
    <details>
      <summary>Node legend</summary>
      <div class="node-grid" id="node-legend"></div>
    </details>
  </div>
  <div class="pane center">
    <details open class="card" style="padding:0">
      <summary style="font-size:12px;color:var(--fg);font-weight:650;padding:12px 14px">How to change a workflow (the honest map)</summary>
      <div class="recipe" id="recipe" style="padding:0 14px 12px"></div>
    </details>
    <h2>Resolved graph</h2>
    <div class="graph-wrap" id="graph-wrap"><div class="muted">Select an entry…</div></div>
    <div id="track-view"></div>
  </div>
  <div class="pane right">
    <h2>Edit</h2>
    <div id="form"><div class="muted">Select an entry to edit.</div></div>
    <h2>Save</h2>
    <div class="row">
      <label class="fld">target</label>
      <select id="save-target" style="width:auto;flex:1">
        <option value="overlay">overlay (this project)</option>
        <option value="baseline">baseline (ALL projects)</option>
      </select>
    </div>
    <div class="row">
      <button class="btn" id="save-btn" onclick="save()" disabled>Save</button>
      <span id="save-status" class="status"></span>
    </div>
    <div class="note">Validated before write (closed vocab + structure). A bad edit is rejected; nothing is written. A <code>.bak</code> of the prior file is kept.</div>
  </div>
</main>

<script>
let STATE = { tab:'shapes', data:null, selected:null, tracks:[], boundTrack:null, boundShape:null, nodes:{} };
const $ = id => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts || {});
  let j; try { j = await r.json(); } catch(e){ j = {ok:false, error:'non-JSON response'}; }
  return j;
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// --- theme (CSS custom properties + data-theme; system follows the OS) ------
function resolveSystem(){ return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
function applyTheme(choice){
  const effective = choice === 'system' ? resolveSystem() : choice;
  document.documentElement.setAttribute('data-theme', effective);
  for (const t of ['dark','light','system']) $('th-'+t).classList.toggle('active', t===choice);
  // the SVG graph recolors via CSS classes automatically; only the inline
  // gradient/track tints (drawn from /api) need no re-render. Re-render the
  // selected graph so any data-derived colors stay consistent.
  if (STATE.selected && STATE.tab === 'shapes') renderGraph();
}

// --- tabs + registry --------------------------------------------------------
function switchTab(tab) {
  STATE.tab = tab; STATE.selected = null;
  $('tab-shapes').classList.toggle('active', tab==='shapes');
  $('tab-task-types').classList.toggle('active', tab==='task-types');
  $('entries-title').textContent = tab==='shapes' ? 'Shapes' : 'Task Types';
  loadRegistry();
}
async function loadRegistry() {
  STATE.data = await api('/api/registry?which='+STATE.tab);
  renderEntries(); renderForm(); renderGraph(); renderFieldGuide();
}
function dataKey(){ return STATE.tab==='shapes' ? 'shapes' : 'tags'; }
function entryDoc(){ return STATE.data && STATE.data.merged ? STATE.data.merged[dataKey()] || {} : {}; }
function defaultDoc(){ return STATE.data && STATE.data.merged ? STATE.data.merged.default || {} : {}; }

function renderEntries() {
  const ul = $('entries'); ul.innerHTML = '';
  if (!STATE.data || (!STATE.data.ok && STATE.data.error)) { ul.innerHTML = '<li class="muted">'+esc(STATE.data && STATE.data.error || 'no data')+'</li>'; return; }
  const entries = entryDoc();
  const names = ['default', ...Object.keys(entries).filter(n=>n!=='default')];
  for (const name of names) {
    const origin = STATE.data.origins[name] || 'baseline';
    const li = document.createElement('li');
    if (name === STATE.selected) li.classList.add('selected');
    li.innerHTML = '<span class="badge '+origin[0]+'">'+origin[0]+'</span><span class="name"></span>';
    li.querySelector('.name').textContent = name;
    li.onclick = () => { STATE.selected = name; renderEntries(); renderForm(); renderGraph(); };
    ul.appendChild(li);
  }
  $('entry-name').disabled = !STATE.selected;
  if (STATE.selected) $('entry-name').value = STATE.selected;
}
function effectiveRow() {
  if (!STATE.selected) return null;
  const ent = entryDoc()[STATE.selected];
  return Object.assign({}, defaultDoc(), ent || {});
}
function fxPill(e) {
  // The drives|intent|display badge for one field; the caller passes the
  // resolved effects entry (renderForm already has it as fx[f]).
  return e ? ' <span class="fx '+e.class+'" title="'+esc(e.text)+'">'+e.class+'</span>' : '';
}
function renderForm() {
  const wrap = $('form');
  if (!STATE.selected) { wrap.innerHTML = '<div class="muted">Select an entry to edit.</div>'; $('save-btn').disabled = true; return; }
  const row = effectiveRow() || {};
  const v = STATE.data.vocab || {};
  const fx = (v.effects)||{};
  let html = '';
  const isNew = !(STATE.selected in entryDoc());
  html += '<div class="field"><label class="fld">name</label><input type="text" id="f-name" value="'+esc(STATE.selected)+'"'+(STATE.selected==='default'?' disabled':'')+'></div>';
  for (const [f, opts] of Object.entries(v.list_fields||{})) {
    const cur = row[f] || [];
    if (opts) {
      html += '<div class="field"><label class="fld">'+esc(f)+fxPill(fx[f])+'</label>'
        + '<select id="f-'+f+'" multiple>'+opts.map(o=>'<option'+(cur.includes(o)?' selected':'')+'>'+esc(o)+'</option>').join('')+'</select>'
        + (fx[f]?'<div class="note" style="margin-top:3px">'+esc(fx[f].text)+'</div>':'')+'</div>';
    } else {
      html += '<div class="field"><label class="fld">'+esc(f)+fxPill(fx[f])+'</label><input type="text" id="f-'+f+'" value="'+esc((cur||[]).join(', '))+'" placeholder="comma, separated"></div>';
    }
  }
  for (const [f, opts] of Object.entries(v.scalar_fields||{})) {
    html += '<div class="field"><label class="fld">'+esc(f)+fxPill(fx[f])+'</label><select id="f-'+f+'">'
      + '<option value=""'+(row[f]===undefined?' selected':'')+'>(inherit)</option>'
      + opts.map(o=>'<option'+(row[f]===o?' selected':'')+'>'+esc(o)+'</option>').join('')+'</select></div>';
  }
  if (v.bool_fields && v.bool_fields.length) {
    html += '<div class="field"><label class="fld">flags</label><div class="checks">';
    for (const f of v.bool_fields) html += '<label><input type="checkbox" id="f-'+f+'"'+(row[f]?' checked':'')+'> '+esc(f)+fxPill(fx[f])+'</label>';
    html += '</div></div>';
  }
  for (const f of (v.text_fields||[])) {
    html += '<div class="field"><label class="fld">'+esc(f)+fxPill(fx[f])+'</label><textarea id="f-'+f+'">'+esc(row[f]||'')+'</textarea></div>';
  }
  if (isNew) html += '<div class="note">This entry does not exist yet — saving creates it.</div>';
  wrap.innerHTML = html;
  $('save-btn').disabled = false;
}
function collectDoc() {
  if (!STATE.selected) return null;
  const v = STATE.data.vocab || {};
  const newName = $('f-name') ? $('f-name').value.trim() : STATE.selected;
  const name = newName || STATE.selected;
  const row = {};
  for (const [f, opts] of Object.entries(v.list_fields||{})) {
    if (opts) {
      const vals = Array.from($('f-'+f).selectedOptions).map(o=>o.value);
      if (vals.length) row[f] = vals;
    } else {
      const vals = $('f-'+f).value.split(',').map(s=>s.trim()).filter(Boolean);
      if (vals.length) row[f] = vals;
    }
  }
  for (const f of Object.keys(v.scalar_fields||{})) { const sel = $('f-'+f); if (sel.value) row[f] = sel.value; }
  for (const f of (v.bool_fields||[])) { if ($('f-'+f).checked) row[f] = true; }
  for (const f of (v.text_fields||[])) { const val = $('f-'+f).value; if (val && val.trim()) row[f] = val; }
  const dk = dataKey();
  const doc = {}; doc[dk] = {}; doc[dk][name] = row;
  if (STATE.data.baseline && STATE.data.baseline._comment) doc._comment = STATE.data.baseline._comment;
  return { name, doc };
}
async function save() {
  const collected = collectDoc();
  if (!collected) return;
  const target = $('save-target').value;
  setStatus('saving…', 'warn');
  const res = await api('/api/registry/save', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({which: STATE.tab, target, doc: collected.doc})});
  if (res.ok) { setStatus('saved → '+target, 'ok'); STATE.selected = collected.name; await loadRegistry(); }
  else { setStatus('rejected: '+(res.errors||[res.error]).join('; '), 'err'); }
}
function setStatus(msg, cls) { const s = $('save-status'); s.textContent = msg; s.className = 'status '+(cls||''); }
function addEntry() {
  const name = prompt('New '+(STATE.tab==='shapes'?'shape':'tag')+' name:');
  if (!name) return;
  STATE.selected = name;
  if (STATE.data && STATE.data.merged) { STATE.data.merged[dataKey()][name] = STATE.data.merged[dataKey()][name] || {}; STATE.data.origins[name] = 'overlay'; }
  renderEntries(); renderForm(); renderGraph();
}

// --- field guide + node legend (education) ----------------------------------
function renderFieldGuide() {
  const wrap = $('field-guide-body');
  if (!STATE.data || !STATE.data.vocab || !STATE.data.vocab.effects) { wrap.innerHTML = '<div class="muted">—</div>'; return; }
  let html = '<div class="note" style="margin:6px 2px">drives = changes dispatch · intent = advisory only · display = reference, not injected</div>';
  for (const [f, e] of Object.entries(STATE.data.vocab.effects)) {
    html += '<div class="fg-row"><span class="fx '+e.class+'">'+e.class+'</span><span class="fn">'+esc(f)+'</span><span class="ft">'+esc(e.text)+'</span></div>';
  }
  wrap.innerHTML = html;
}
function renderNodeLegend() {
  const wrap = $('node-legend');
  const nodes = STATE.nodes || {};
  let html = '';
  for (const [name, d] of Object.entries(nodes)) {
    html += '<div class="node-row"><span class="nk '+d.kind+'">'+esc(d.kind)+'</span><div><div class="nm">'+esc(name)+'</div>'
      + '<div class="ds">'+esc(d.role)+'</div>'
      + '<div class="pr">produces: '+esc(d.produces)+'</div></div></div>';
  }
  wrap.innerHTML = html || '<div class="muted">—</div>';
}

// --- resolved graph (SVG; data-driven, themed via CSS classes) --------------
function renderGraph() {
  const wrap = $('graph-wrap');
  if (!STATE.selected) { wrap.innerHTML = '<div class="muted">Select an entry…</div>'; return; }
  if (STATE.tab !== 'shapes') {
    const row = effectiveRow() || {};
    wrap.innerHTML = '<div class="muted">Task-type profile</div>'
      + '<div class="row" style="margin-top:8px"><span class="pill">route: '+esc(row.route||'executor (inherit)')+'</span>'
      + (row.tdd_exempt?'<span class="pill">tdd-exempt</span>':'')
      + (row.coverage_exempt?'<span class="pill">coverage-exempt</span>':'')
      + (row.refactor?'<span class="pill">+ tactical refactor</span>':'')
      + '</div>'
      + (row.workflow?'<div class="wf" style="margin-top:8px"><b>workflow (injected into task-executor):</b><br>'+esc(row.workflow)+'</div>':'<div class="note" style="margin-top:8px">no bespoke workflow → runs default TDD (Steps 3-8)</div>')
      + '<div class="note" style="margin-top:8px">'+esc(row.when_to_use||'(no when_to_use)')+'</div>';
    return;
  }
  const name = STATE.selected;
  fetch('/api/resolve?shape='+encodeURIComponent(name)).then(r=>r.json()).then(g=>{ wrap.innerHTML = shapeSVG(g); });
}
function shapeSVG(g) {
  const nodes = g.nodes||[], verifiers = g.verifiers||[], gates = g.gates||[];
  const allGates = ['tdd','coverage','checkpoint'];
  const nodeW=154, nodeH=46, gap=34;
  const W = Math.max(600, nodes.length*(nodeW+gap)+10), H = 230;
  const live = STATE.boundShape && STATE.boundShape === g.shape;
  let s = '<svg width="100%" viewBox="0 0 '+W+' '+H+'" font-size="12" font-family="inherit" role="img" aria-label="resolved workflow graph for '+esc(g.shape)+'">';
  s += '<defs>'
    + '<linearGradient id="sgng" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--acc)"/><stop offset="1" style="stop-color:var(--acc-2)"/></linearGradient>'
    + '<filter id="sgglow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="3.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
    + '<marker id="sgarr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path class="sg-arr" d="M0,0 L7,4 L0,8 z"/></marker>'
    + '</defs>';
  nodes.forEach((n,i)=>{ if(i>0){ const x=10+i*(nodeW+gap), px=x-gap; s += '<path class="sg-conn" d="M'+(px+2)+' '+(20+nodeH/2)+' L'+(x-2)+' '+(20+nodeH/2)+'" marker-end="url(#sgarr)"/>'; } });
  nodes.forEach((n,i)=>{
    const x = 10 + i*(nodeW+gap);
    s += '<rect class="sg-spine'+(live?' glow':'')+'" x="'+x+'" y="20" width="'+nodeW+'" height="'+nodeH+'" rx="11"/>';
    s += '<text class="sg-node-text" x="'+(x+nodeW/2)+'" y="47" text-anchor="middle">'+esc(n)+'</text>';
  });
  s += '<text class="sg-label" x="10" y="100">checkpoint verifiers · load-bearing</text>';
  if (!verifiers.length) s += '<text class="sg-label" x="10" y="128">(none — no verifier fans out at the checkpoint)</text>';
  verifiers.forEach((vn,i)=>{
    const x = 10 + i*(nodeW+gap);
    s += '<rect class="sg-verif" x="'+x+'" y="108" width="'+nodeW+'" height="36" rx="10"/>';
    s += '<text class="sg-node-text" x="'+(x+nodeW/2)+'" y="131" text-anchor="middle">'+esc(vn)+'</text>';
  });
  s += '<text class="sg-label" x="10" y="172">track gates · load-bearing</text>';
  allGates.forEach((gg,i)=>{
    const on = gates.includes(gg), x = 10 + i*(nodeW+gap);
    s += '<rect class="'+(on?'sg-gate-on':'sg-gate-off')+'" x="'+x+'" y="180" width="'+nodeW+'" height="34" rx="9"/>';
    s += '<text class="'+(on?'sg-gate-on-txt':'sg-gate-off-txt')+'" x="'+(x+14)+'" y="201">'+(on?'▣':'▢')+' '+esc(gg)+'</text>';
  });
  s += '</svg>';
  s += '<div class="row" style="margin-top:8px">'
    + '<span class="pill">verify_policy: '+esc(g.verify_policy||'—')+'</span>'
    + '<span class="pill">stop: '+esc(g.stop_condition||'—')+'</span>'
    + '<span class="pill">ac_grounding: '+esc(g.ac_grounding||'—')+'</span>'
    + (live?'<span class="fx drives">live · bound track</span>':'')+'</div>';
  if (STATE.boundShape && STATE.boundShape !== g.shape) {
    s += '<div class="note">graph for <b>'+esc(g.shape)+'</b>; bound track uses <b>'+esc(STATE.boundShape)+'</b>.</div>';
  }
  return s;
}

// --- track binding + whole-track live map -----------------------------------
async function loadTracks() {
  const res = await api('/api/tracks');
  STATE.tracks = res.tracks || [];
  const sel = $('track-select');
  sel.innerHTML = '<option value="">(none)</option>';
  for (const t of STATE.tracks) {
    const o = document.createElement('option');
    o.value = t.dir; o.textContent = t.track_id+' ['+(t.status||'?')+'] · '+t.workflow_shape;
    sel.appendChild(o);
  }
}
function onTrackChange() {
  const dir = $('track-select').value;
  STATE.boundTrack = dir || null;
  if (!dir) { STATE.boundShape = null; $('track-shape-info').innerHTML=''; $('track-view').innerHTML=''; renderGraph(); return; }
  const t = STATE.tracks.find(x=>x.dir===dir);
  STATE.boundShape = t ? t.workflow_shape : null;
  $('track-shape-info').innerHTML = 'shape <b>'+esc(STATE.boundShape||'?')+'</b> '
    + '<button class="btn ghost" onclick="bindShape()" style="padding:2px 8px;margin-left:8px;font-size:11px">bind current selection</button>';
  renderGraph();
  fetch('/api/resolve?track='+encodeURIComponent(dir)).then(r=>r.json()).then(env=>{ renderTrackView(env); }).catch(()=>{ $('track-view').innerHTML=''; });
}
async function bindShape() {
  if (!STATE.boundTrack || !STATE.selected) return;
  const res = await api('/api/track/shape', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({track_dir: STATE.boundTrack, shape: STATE.selected})});
  if (res.ok) { STATE.boundShape = STATE.selected; setStatus('track bound → '+STATE.selected,'ok'); await loadTracks(); $('track-select').value=STATE.boundTrack; onTrackChange(); }
  else { setStatus('bind failed: '+(res.error||res.errors),'err'); }
}
function statusLabel(s){ return ({completed:'✓ done', in_progress:'◌ running', pending:'○ pending', failed:'✗ failed', skipped:'⊘ skipped', deferred:'⏳ deferred'}[s])||s; }
function isHere(c, pos){
  return pos.phase!=null && +pos.phase===+c.phase && +pos.task===+c.task &&
    ((pos.subtask==null && c.subtask==null) || (pos.subtask!=null && +pos.subtask===+c.subtask));
}
function taskCardHTML(c, pos) {
  const here = isHere(c, pos);
  let h = '<div class="tcard'+(here?' here':'')+'">'
    + '<div class="idx">'+(c.subtask!=null?(c.phase+'.'+c.task+'.'+c.subtask):(c.phase+'.'+c.task))+'</div>'
    + '<div class="body"><div class="tn">'+esc(c.name||'(unnamed)')+'</div>'
    + '<div class="meta"><span class="stat '+(c.status||'pending')+'">'+esc(statusLabel(c.status))+'</span>';
  if (c.tag) h += '<span class="tag-chip'+(c.known?'':' unknown')+'" title="'+esc(c.when_to_use||'')+'">['+esc(c.tag)+']</span>';
  else h += '<span class="pill sm">default TDD</span>';
  if (c.tdd_exempt) h += '<span class="pill sm">tdd-exempt</span>';
  if (c.coverage_exempt) h += '<span class="pill sm">cov-exempt</span>';
  if (c.refactor) h += '<span class="pill sm">+ refactor</span>';
  if (c.coverage_pct!=null) h += '<span class="pill sm">'+c.coverage_pct+'% cov</span>';
  h += '</div>';
  if (c.workflow) h += '<div class="wf">'+esc(c.workflow)+'</div>';
  else if (c.tag) h += '<div class="note" style="margin-top:5px">runs default TDD (no bespoke workflow prose for this tag)</div>';
  h += '</div></div>';
  return h;
}
function renderTrackView(env) {
  const wrap = $('track-view');
  if (!env || env.error) { wrap.innerHTML = '<div class="muted">'+esc(env && env.error || 'no track data')+'</div>'; return; }
  const rw = env.resolved_workflow || {}, pos = rw.position || {}, cards = (env.studio && env.studio.task_cards) || [], q = env.quality || {};
  let html = '<div class="card" style="margin-top:14px">'
    + '<h2 style="margin:0 0 8px">Bound track: '+esc((env.track&&env.track.track_id)||'?')+'</h2>'
    + '<div class="row" style="margin:0">'
    + '<span class="pill">shape: '+esc(rw.shape||'?')+'</span>'
    + '<span class="pill">verifiers: '+esc((rw.verifiers||[]).join(', ')||'—')+'</span>'
    + '<span class="pill">gates: '+esc((rw.gates||[]).join(', ')||'—')+'</span>'
    + (q.completion_pct!=null?'<span class="pill">'+q.completion_pct+'% done</span>':'')
    + (q.coverage_pct!=null?'<span class="pill">'+q.coverage_pct+'% cov</span>':'')
    + '</div>'
    + '<div class="note" style="margin-top:8px">'+(pos.phase!=null?('► you are here — Phase '+pos.phase+(pos.task!=null?' · Task '+pos.task:'')+(pos.name?' — '+esc(pos.name):'')):'no active task')+'</div>'
    + '</div>';
  const byPhase = {};
  for (const c of cards) { (byPhase[c.phase]=byPhase[c.phase]||[]).push(c); }
  html += '<h2>Task map · per-task workflow</h2>';
  if (!cards.length) html += '<div class="muted">no tasks</div>';
  for (const phStr of Object.keys(byPhase).sort((a,b)=>+a-+b)) {
    const phCards = byPhase[phStr];
    html += '<div class="phase"><div class="phase-h">Phase '+esc(phStr)+'</div>';
    const taskCards = phCards.filter(c=>c.subtask==null);
    for (const tc of taskCards) {
      html += taskCardHTML(tc, pos);
      const subs = phCards.filter(c=>c.subtask!=null && +c.task===+tc.task);
      for (const sc of subs) html += '<div class="sub">'+taskCardHTML(sc, pos)+'</div>';
    }
    html += '</div>';
  }
  wrap.innerHTML = html;
}

// --- recipe (the honesty / how-to-change surface) ---------------------------
function renderRecipe() {
  $('recipe').innerHTML =
    '<span class="kb">Before you edit: what actually changes dispatch?</span>'
    + '<div>• <span class="drives">Drives dispatch</span> — edit <code>verifiers</code> (which run at the checkpoint — ac-tracer → build-runner → test-runner, cheapest-first), <code>gates</code> (tdd / coverage / checkpoint), <code>checkpoint_policy</code> (whether the checkpoint runs at all), and <code>ac_grounding</code> (how AC are grounded: <b>test</b> vs <b>review</b> — the substitute that lets a non-code shape drop the build/test tiers). These are the ONLY shape fields that change dispatch behavior.</div>'
    + '<div>• <span class="intent">Intent only</span> — <code>nodes</code> declares topology but does <b>not</b> reorder dispatch (the planner→executor→checker spine is hardcoded). It records intent and surfaces a <code>shape_violation</code> when reality drifts.</div>'
    + '<div>• Per-task behavior (migrate-vs-TDD, routing, exemptions) lives in the <b>Task Types</b> registry (<code>workflow</code> prose), not the shape.</div>'
    + '<div>• <code>verify_policy</code> / <code>stop_condition</code> / <code>instruction</code> are display/reference — not injected into any prompt.</div>'
    + '<div>• Target: <code>overlay</code> = this project only; <code>baseline</code> = ships to ALL projects. Choose deliberately.</div>';
}

// boot
(function init(){
  renderRecipe();
  fetch('/api/state').then(r=>r.json()).then(s=>{
    if (s && s.default_target) { const el=$('save-target'); if (el) el.value = s.default_target; }
    applyTheme((s && s.theme) || 'system');
  }).catch(()=>{ applyTheme('system'); });
  fetch('/api/nodes').then(r=>r.json()).then(d=>{ STATE.nodes = d.nodes||{}; renderNodeLegend(); }).catch(()=>{});
  loadRegistry();
  loadTracks();
})();
</script>
</body>
</html>
"""
