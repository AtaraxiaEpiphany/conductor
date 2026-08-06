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
from . import workflow_shapes as ws
from .helpers import flag
from .misc import build_view_envelope


# --- vocab + graph helpers (pure; shared with the resolve endpoint) ------------

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
            },
            "text_fields": ["instruction", "when_to_use"],
            # `nodes` is ADVISORY; `verifiers`/`gates` are LOAD-BEARING — the UI
            # surfaces the distinction so an editor knows editing nodes does not
            # reorder dispatch (non-negotiable #8).
            "load_bearing": ["verifiers", "gates"],
            "advisory": ["nodes"],
        },
        "task-types": {
            "scalar_fields": {"route": list(rv.ROUTES)},
            "bool_fields": ["tdd_exempt", "coverage_exempt", "refactor",
                            "auto_propose", "over_tag_risk"],
            "text_fields": ["when_to_use", "workflow"],
            "list_fields": {"signals": None},  # free-form keyword strings
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
    }


def _validate_track_dir(track_dir, project_dir):
    """Security gate for client-supplied track paths. Returns a resolved Path or None.

    The shape-binding endpoint is the ONE place a client names a filesystem path.
    It must clear three checks before it reaches :func:`load`/:func:`save`:

    1. **No traversal / absolute escape.** Resolved plainly (no ``..`` games).
    2. **It is actually a track** (a ``track-state.json`` sibling exists).
    3. **It is under the project's ``conductor/tracks/`` tree** when a project
       dir is known — so a local studio cannot be pointed at, say, the plugin's
       own source via a crafted ``track_dir``.

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
            except ValueError as exc:
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
                _json_response(self, env)
                return
            _json_response(self, {"ok": False, "error": "need ?shape= or ?track="}, 400)
            return
        if path == "/api/tracks":
            _json_response(self, {"tracks": rs.list_tracks(self._project_dir)})
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
            result = rs.save_registry(which, target, doc, self._project_dir)
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
            result = rs.set_workflow_shape(str(resolved), shape)
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
    [--project-dir DIR] [--baseline] [--no-browser]``.

    ``--baseline`` is a convenience that flips the studio's DEFAULT write target
    hint to the plugin baseline (an advanced, ships-to-every-project edit); the
    frontend still offers the toggle. Parses flags from the raw argv slice
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
    # baseline_default is surfaced to the frontend via a data attribute on the
    # served page (the only channel from CLI flag to the SPA, since the page is a
    # static string). We patch the placeholder before serving by stashing it on
    # the state object the handler reads — simpler than templating the HTML.
    _PAGE_STATE["default_target"] = "baseline" if baseline_default else "overlay"
    serve(port=port_val, host=host, project_dir=project_dir, open_browser=open_browser)


# The served page reads this for the CLI's --baseline default. A module-level
# dict (not a constant) so cmd_shape_studio can set it per-invocation.
_PAGE_STATE = {"default_target": "overlay"}


# --- the frontend (one HTML string; vanilla JS + inline SVG/CSS) ---------------
# Kept inline so the studio is genuinely one file with zero asset dependencies —
# no separate JS/CSS to serve, no build step, no CDN. The JS is data-driven from
# /api/vocab + /api/registry so both registries render through one code path.

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Conductor Workflow Studio</title>
<style>
  :root { color-scheme: light; --bd:#d0d7de; --bg:#f6f8fa; --fg:#1f2328;
          --acc:#0969da; --warn:#9a6700; --ok:#1a7f37; --err:#cf222e;
          --base:#6e7781; --over:#bf8700; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;
         color:var(--fg); background:var(--bg); }
  header { display:flex; gap:12px; align-items:center; padding:10px 16px;
           background:#fff; border-bottom:1px solid var(--bd); flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  header .spacer { flex:1; }
  .tabs button { background:none; border:1px solid transparent; padding:6px 12px;
                 cursor:pointer; border-radius:6px 6px 0 0; font:inherit; }
  .tabs button.active { background:var(--bg); border-color:var(--bd); border-bottom:0; }
  main { display:grid; grid-template-columns: 260px 1fr 360px; gap:0; height:calc(100vh - 52px); }
  .pane { background:#fff; border-right:1px solid var(--bd); overflow:auto; padding:10px; }
  .pane.center { border-right:1px solid var(--bd); }
  .pane.right { border-right:0; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--base); margin:12px 0 6px; }
  ul.entries { list-style:none; margin:0; padding:0; }
  ul.entries li { padding:6px 8px; border-radius:6px; cursor:pointer; display:flex; align-items:center; gap:8px; }
  ul.entries li:hover { background:var(--bg); }
  ul.entries li.selected { background:#ddf4ff; }
  .badge { font-size:10px; font-weight:700; padding:1px 5px; border-radius:8px; border:1px solid; }
  .badge.B { color:var(--base); border-color:var(--base); }
  .badge.O { color:var(--over); border-color:var(--over); }
  .muted { color:var(--base); font-size:12px; }
  .row { display:flex; gap:8px; align-items:center; margin:6px 0; flex-wrap:wrap; }
  label { font-size:12px; color:var(--base); min-width:96px; }
  input[type=text], select, textarea { font:inherit; padding:4px 6px; border:1px solid var(--bd);
          border-radius:6px; background:#fff; color:var(--fg); width:100%; }
  textarea { min-height:54px; resize:vertical; }
  .field { margin:8px 0; }
  .checks { display:flex; gap:12px; flex-wrap:wrap; }
  .checks label { min-width:0; display:flex; gap:4px; align-items:center; }
  select[multiple] { min-height:70px; }
  .btn { background:var(--acc); color:#fff; border:0; border-radius:6px; padding:6px 12px;
         cursor:pointer; font:inherit; }
  .btn.ghost { background:#fff; color:var(--fg); border:1px solid var(--bd); }
  .btn:disabled { opacity:.5; cursor:not-allowed; }
  .status { font-size:12px; padding:4px 8px; border-radius:6px; }
  .status.ok { color:var(--ok); } .status.err { color:var(--err); } .status.warn{color:var(--warn);}
  .svg-wrap { background:var(--bg); border:1px solid var(--bd); border-radius:8px; padding:12px; }
  .pill { font-size:11px; padding:2px 6px; border-radius:10px; background:var(--bg); border:1px solid var(--bd); }
  .note { font-size:11px; color:var(--base); font-style:italic; }
  details { margin:6px 0; }
  summary { cursor:pointer; font-size:12px; color:var(--base); }
</style>
</head>
<body>
<header>
  <h1>🎛️ Conductor Workflow Studio</h1>
  <div class="tabs">
    <button id="tab-shapes" class="active" onclick="switchTab('shapes')">Workflow Shapes</button>
    <button id="tab-task-types" onclick="switchTab('task-types')">Task Types</button>
  </div>
  <span class="spacer"></span>
  <label class="muted">Track:
    <select id="track-select" onchange="onTrackChange()" style="width:auto">
      <option value="">(no track bound)</option>
    </select>
  </label>
  <span id="track-shape-info" class="muted"></span>
</header>
<main>
  <div class="pane left">
    <div class="row" style="justify-content:space-between; align-items:center">
      <h2 style="margin:0" id="entries-title">Shapes</h2>
      <button class="btn ghost" onclick="addEntry()" style="padding:2px 8px">+ new</button>
    </div>
    <input type="text" id="entry-name" placeholder="(select or add)" disabled>
    <ul class="entries" id="entries"></ul>
    <h2>Origin key</h2>
    <div class="muted"><span class="badge B">B</span> plugin baseline &nbsp; <span class="badge O">O</span> project overlay</div>
  </div>
  <div class="pane center">
    <h2>Resolved graph</h2>
    <div class="svg-wrap" id="graph-wrap"><div class="muted">Select an entry…</div></div>
    <div id="track-preview" class="muted" style="margin-top:8px"></div>
  </div>
  <div class="pane right">
    <h2>Edit</h2>
    <div id="form"><div class="muted">Select an entry to edit.</div></div>
    <h2>Save</h2>
    <div class="row">
      <label>target</label>
      <select id="save-target" style="width:auto">
        <option value="overlay">overlay (this project)</option>
        <option value="baseline">baseline (advanced — ships to ALL projects)</option>
      </select>
    </div>
    <div class="row">
      <button class="btn" id="save-btn" onclick="save()" disabled>Save</button>
      <span id="save-status" class="status"></span>
    </div>
    <div class="note">Saves are validated before write (closed vocab + structure). A bad edit is rejected; nothing is written. A <code>.bak</code> of the prior file is kept.</div>
  </div>
</main>

<script>
let STATE = { tab:'shapes', data:null, selected:null, tracks:[], boundTrack:null, boundShape:null };
const $ = id => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts || {});
  let j; try { j = await r.json(); } catch(e){ j = {ok:false, error:'non-JSON response'}; }
  return j;
}

function switchTab(tab) {
  STATE.tab = tab; STATE.selected = null;
  $('tab-shapes').classList.toggle('active', tab==='shapes');
  $('tab-task-types').classList.toggle('active', tab==='task-types');
  $('entries-title').textContent = tab==='shapes' ? 'Shapes' : 'Task Types';
  loadRegistry();
}

async function loadRegistry() {
  STATE.data = await api('/api/registry?which='+STATE.tab);
  renderEntries(); renderForm(); renderGraph();
}

function dataKey(){ return STATE.tab==='shapes' ? 'shapes' : 'tags'; }
function entryDoc(){ return STATE.data && STATE.data.merged ? STATE.data.merged[dataKey()] || {} : {}; }
function defaultDoc(){ return STATE.data && STATE.data.merged ? STATE.data.merged.default || {} : {}; }

function renderEntries() {
  const ul = $('entries'); ul.innerHTML = '';
  if (!STATE.data || !STATE.data.ok && STATE.data.error) { ul.innerHTML = '<li class="muted">'+(STATE.data?.error||'no data')+'</li>'; return; }
  const entries = entryDoc();
  // default block first (the fail-open fallback), then the rest.
  const names = ['default', ...Object.keys(entries).filter(n=>n!=='default')];
  for (const name of names) {
    const origin = STATE.data.origins[name] || 'baseline';
    const li = document.createElement('li');
    if (name === STATE.selected) li.classList.add('selected');
    li.innerHTML = '<span class="badge ' + origin[0] + '">' + origin[0] + '</span><span></span>';
    li.querySelector('span:last-child').textContent = name;
    li.onclick = () => { STATE.selected = name; renderEntries(); renderForm(); renderGraph(); };
    ul.appendChild(li);
  }
  $('entry-name').disabled = !STATE.selected;
  if (STATE.selected) $('entry-name').value = STATE.selected;
}

function effectiveRow() {
  // The row as the conductor resolves it: default ⊕ entry overrides (so the
  // editor shows inherited values, not blanks, for partial rows).
  if (!STATE.selected) return null;
  const ent = entryDoc()[STATE.selected];
  return Object.assign({}, defaultDoc(), ent || {});
}

function renderForm() {
  const wrap = $('form');
  if (!STATE.selected) { wrap.innerHTML = '<div class="muted">Select an entry to edit.</div>'; $('save-btn').disabled = true; return; }
  const row = effectiveRow() || {};
  const v = STATE.data.vocab || {};
  let html = '';
  const isNew = !(STATE.selected in entryDoc());
  html += '<div class="field"><label>name</label><input type="text" id="f-name" value="'+esc(STATE.selected)+'"'+(STATE.selected==='default'?' disabled':'')+'></div>';
  // list fields (multi-select from vocab)
  for (const [f, opts] of Object.entries(v.list_fields||{})) {
    const cur = row[f] || [];
    if (opts) {
      html += '<div class="field"><label>'+f+(v.load_bearing&&v.load_bearing.includes(f)?' <span class="pill">load-bearing</span>':v.advisory&&v.advisory.includes(f)?' <span class="pill">advisory</span>':'')+'</label>'
        + '<select id="f-'+f+'" multiple>'+opts.map(o=>'<option'+(cur.includes(o)?' selected':'')+'>'+o+'</option>').join('')+'</select></div>';
    } else {
      // free-form list (signals): comma-joined
      html += '<div class="field"><label>'+f+'</label><input type="text" id="f-'+f+'" value="'+esc((cur||[]).join(', '))+'" placeholder="comma, separated"></div>';
    }
  }
  // scalar fields (dropdown)
  for (const [f, opts] of Object.entries(v.scalar_fields||{})) {
    html += '<div class="field"><label>'+f+'</label><select id="f-'+f+'">'
      + '<option value=""'+(row[f]===undefined?' selected':'')+'>(inherit)</option>'
      + opts.map(o=>'<option'+(row[f]===o?' selected':'')+'>'+o+'</option>').join('')+'</select></div>';
  }
  // bool fields (checkboxes)
  if (v.bool_fields && v.bool_fields.length) {
    html += '<div class="field"><label>flags</label><div class="checks">';
    for (const f of v.bool_fields) html += '<label><input type="checkbox" id="f-'+f+'"'+(row[f]?' checked':'')+'> '+f+'</label>';
    html += '</div></div>';
  }
  // text fields (textarea)
  for (const f of (v.text_fields||[])) {
    html += '<div class="field"><label>'+f+'</label><textarea id="f-'+f+'">'+esc(row[f]||'')+'</textarea></div>';
  }
  if (isNew) html += '<div class="note">This entry does not exist yet — saving creates it.</div>';
  wrap.innerHTML = html;
  $('save-btn').disabled = false;
}

function collectDoc() {
  // Gather the edited row into a doc fragment {dataKey: {name: row}}.
  if (!STATE.selected) return null;
  const v = STATE.data.vocab || {};
  const newName = $('f-name') ? $('f-name').value.trim() : STATE.selected;
  const name = newName || STATE.selected;
  const row = {};
  for (const [f, opts] of Object.entries(v.list_fields||{})) {
    if (opts) {
      const sel = $('f-'+f);
      const vals = Array.from(sel.selectedOptions).map(o=>o.value);
      if (vals.length) row[f] = vals;
    } else {
      const val = $('f-'+f).value;
      const vals = val.split(',').map(s=>s.trim()).filter(Boolean);
      if (vals.length) row[f] = vals;
    }
  }
  for (const f of Object.keys(v.scalar_fields||{})) {
    const sel = $('f-'+f);
    if (sel.value) row[f] = sel.value;
  }
  for (const f of (v.bool_fields||[])) {
    if ($('f-'+f).checked) row[f] = true;
  }
  for (const f of (v.text_fields||[])) {
    const val = $('f-'+f).value;
    if (val && val.trim()) row[f] = val;
  }
  const dk = dataKey();
  const doc = {}; doc[dk] = {}; doc[dk][name] = row;
  // round-trip the doc blocks the editor must preserve
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
  if (res.ok) {
    setStatus('saved → '+target, 'ok');
    STATE.selected = collected.name;
    await loadRegistry();
  } else {
    setStatus('rejected: '+(res.errors||[res.error]).join('; '), 'err');
  }
}

function setStatus(msg, cls) {
  const s = $('save-status'); s.textContent = msg; s.className = 'status '+(cls||'');
}

function addEntry() {
  const name = prompt('New '+(STATE.tab==='shapes'?'shape':'tag')+' name:');
  if (!name) return;
  STATE.selected = name;
  // Temporarily inject an empty row so the form/graph render for it.
  if (STATE.data && STATE.data.merged) {
    STATE.data.merged[dataKey()][name] = STATE.data.merged[dataKey()][name] || {};
    STATE.data.origins[name] = 'overlay';
  }
  renderEntries(); renderForm(); renderGraph();
}

function renderGraph() {
  const wrap = $('graph-wrap');
  if (!STATE.selected) { wrap.innerHTML = '<div class="muted">Select an entry…</div>'; return; }
  if (STATE.tab !== 'shapes') {
    // task-type: show route + exemptions as a compact card (no topology graph)
    const row = effectiveRow() || {};
    wrap.innerHTML = '<div class="muted">Task-type profile</div>'
      + '<div class="row"><span class="pill">route: '+(row.route||'executor (inherit)')+'</span>'
      + (row.tdd_exempt?'<span class="pill">tdd-exempt</span>':'')
      + (row.coverage_exempt?'<span class="pill">coverage-exempt</span>':'')
      + (row.refactor?'<span class="pill">refactor</span>':'')
      + '</div>'
      + '<div class="note">'+esc(row.when_to_use||'(no when_to_use)')+'</div>';
    return;
  }
  // shape: resolved graph via the accessors (fail-open default for unknown)
  const name = STATE.selected;
  fetch('/api/resolve?shape='+encodeURIComponent(name)).then(r=>r.json()).then(g=>{
    wrap.innerHTML = shapeSVG(g);
  });
}

function shapeSVG(g) {
  const nodes = g.nodes||[];
  const verifiers = g.verifiers||[];
  const gates = g.gates||[];
  const allGates = ['tdd','coverage','checkpoint'];
  // hand-laid: spine left→right, verifiers below, gates row at bottom. No layout engine.
  const nodeW = 150, nodeH = 44, gap = 36;
  const W = Math.max(560, nodes.length*(nodeW+gap));
  const H = 220;
  let svg = '<svg width="100%" viewBox="0 0 '+W+' '+H+'" font-size="12" font-family="inherit">';
  // spine
  nodes.forEach((n,i)=>{
    const x = 10 + i*(nodeW+gap);
    svg += '<rect x="'+x+'" y="20" width="'+nodeW+'" height="'+nodeH+'" rx="8" fill="#fff" stroke="#0969da"/>';
    svg += '<text x="'+(x+nodeW/2)+'" y="46" text-anchor="middle">'+esc(n)+'</text>';
    if (i>0) { const px=x-gap; svg += '<path d="M'+px+' '+(20+nodeH/2)+' L'+x+' '+(20+nodeH/2)+'" stroke="#0969da" stroke-width="2" marker-end="url(#arr)"/>'; }
  });
  // verifiers row
  svg += '<text x="10" y="100" fill="#6e7781">checkpoint verifiers (load-bearing)</text>';
  verifiers.forEach((vn,i)=>{
    const x = 10 + i*(nodeW+gap);
    svg += '<rect x="'+x+'" y="108" width="'+nodeW+'" height="34" rx="8" fill="#fff" stroke="#bf8700" stroke-dasharray="4"/>';
    svg += '<text x="'+(x+nodeW/2)+'" y="129" text-anchor="middle">'+esc(vn)+'</text>';
  });
  // gates row
  svg += '<text x="10" y="170" fill="#6e7781">track gates</text>';
  allGates.forEach((gg,i)=>{
    const on = gates.includes(gg); const x = 10 + i*(nodeW+gap);
    svg += '<rect x="'+x+'" y="178" width="'+nodeW+'" height="32" rx="8" fill="'+(on?'#ddf4ff':'#f6f8fa')+'" stroke="'+(on?'#1a7f37':'#d0d7de')+'"/>';
    svg += '<text x="'+(x+14)+'" y="198">'+(on?'▣':'▢')+' '+gg+'</text>';
  });
  svg += '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L7,4 L0,8 z" fill="#0969da"/></marker></defs>';
  svg += '</svg>';
  svg += '<div class="row" style="margin-top:6px"><span class="pill">verify_policy: '+(g.verify_policy||'—')+'</span>'
    + '<span class="pill">stop: '+(g.stop_condition||'—')+'</span>'
    + '<span class="pill">ac_grounding: '+(g.ac_grounding||'—')+'</span></div>';
  if (STATE.boundShape && STATE.boundShape !== name) {
    svg += '<div class="note">note: this is the graph for "'+esc(name)+'". The bound track currently uses "'+esc(STATE.boundShape)+'".</div>';
  }
  return svg;
}

// --- track binding -------------------------------------------------------
async function loadTracks() {
  const res = await api('/api/tracks');
  STATE.tracks = res.tracks || [];
  const sel = $('track-select');
  sel.innerHTML = '<option value="">(no track bound)</option>';
  for (const t of STATE.tracks) {
    const o = document.createElement('option');
    o.value = t.dir; o.textContent = t.track_id+' ['+(t.status||'?')+'] · '+t.workflow_shape;
    sel.appendChild(o);
  }
}
function onTrackChange() {
  const dir = $('track-select').value;
  STATE.boundTrack = dir || null;
  if (!dir) { STATE.boundShape = null; $('track-shape-info').textContent=''; $('track-preview').innerHTML=''; return; }
  const t = STATE.tracks.find(x=>x.dir===dir);
  STATE.boundShape = t ? t.workflow_shape : null;
  $('track-shape-info').innerHTML = 'shape: <b>'+esc(STATE.boundShape||'?')+'</b> '
    + '<button class="btn ghost" onclick="bindShape()" style="padding:1px 6px;margin-left:8px">set current selection</button>';
  renderGraph();
  // live position preview
  fetch('/api/resolve?track='+encodeURIComponent(dir)).then(r=>r.json()).then(env=>{
    if (env && env.resolved_workflow) {
      const pos = env.resolved_workflow.position||{};
      const loc = pos.phase!=null ? ('Phase '+pos.phase+(pos.task!=null?' · Task '+pos.task:'')) : 'no tasks';
      $('track-preview').innerHTML = 'live: '+loc+(pos.name?' — '+esc(pos.name):'')+' · verifiers ['+env.resolved_workflow.verifiers.join(', ')+']';
    }
  }).catch(()=>{ $('track-preview').innerHTML=''; });
}
async function bindShape() {
  if (!STATE.boundTrack || !STATE.selected) return;
  const res = await api('/api/track/shape', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({track_dir: STATE.boundTrack, shape: STATE.selected})});
  if (res.ok) { STATE.boundShape = STATE.selected; setStatus('track bound → '+STATE.selected,'ok'); await loadTracks(); $('track-select').value=STATE.boundTrack; onTrackChange(); }
  else { setStatus('bind failed: '+(res.error||res.errors),'err'); }
}

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// boot
(function init(){
  // Reflect the CLI --baseline default onto the save-target dropdown (the one
  // channel from a per-invocation flag to this statically-served SPA).
  fetch('/api/state').then(r=>r.json()).then(s=>{
    if (s && s.default_target) { const el=$('save-target'); if (el) el.value = s.default_target; }
  }).catch(()=>{});
  loadRegistry();
  loadTracks();
})();
</script>
</body>
</html>
"""
