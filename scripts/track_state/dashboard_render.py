"""Unicode dashboard renderer for ``track-state view --render``.

Pure stdlib (Unicode box-drawing — no ``rich``/``textual``; the conductor ships
zero pip deps and this renderer is not going to be the first). Rendered strictly
FROM the envelope dict produced by :func:`misc.cmd_view` — it never re-reads
``track-state.json`` and never hardcodes the default-shape topology, so a
``migration`` shape or a future project-overlay shape renders for free.

The graph's value is the **gate / verifier annotation + the position marker**,
not a layout engine: the planner→executor→checker spine barely varies across
shapes, so this is a near-fixed DAG with live annotations rather than a
general graph layout routine. (See ``conductor/design`` graph-engineering
notes: draw the edges before you code, keep anchors frozen, don't
over-engineer.)
"""

# Display order + labels for the gates row. Each entry is (registry key,
# firewall id, human label). Registry order is irrelevant to the reader; F2/F3/F5
# order matches the Execution Firewall numbering.
_GATE_LABELS = (
    ("tdd", "F2", "TDD"),
    ("coverage", "F3", "coverage"),
    ("checkpoint", "F5", "checkpoint"),
)

# Runtime task state → checkbox marker (mirrors core-contract Task State Model).
_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
    "failed": "[!]",
    "skipped": "[>]",
    "deferred": "[d]",
    "blocked": "[#]",
    "cancelled": "[-]",
    "archived": "[x]",
}


def render(envelope):
    """Compose the full Unicode snapshot from a ``cmd_view`` envelope."""
    lines = []
    lines.extend(_header(envelope))
    lines.extend(_graph(envelope))
    lines.extend(_tree(envelope))
    lines.extend(_gauges(envelope))
    return "\n".join(lines)


def _header(envelope):
    t = envelope.get("track") or {}
    parts = [f"TRACK {t.get('track_id') or '(no id)'}"]
    if t.get("shape"):
        parts.append(f"shape: {t['shape']}")
    parts.append(f"mode: {t.get('execution_mode') or '—'}")
    if t.get("status"):
        parts.append(f"status: {t['status']}")
    return ["┌─ " + " · ".join(parts) + " " + "─" * 60]


def _graph(envelope):
    rw = envelope.get("resolved_workflow") or {}
    gates = set(rw.get("gates") or ())
    nodes = rw.get("nodes") or []
    verifiers = rw.get("verifiers") or []
    out = ["│  RESOLVED WORKFLOW"]
    if nodes:
        out.append("│    " + " ──▶ ".join(nodes))
    if verifiers:
        cells = list(verifiers)
        # Surface the code-free narrowing as an annotation (the envelope already
        # reflects the drop; this just makes it legible rather than silent).
        if "test-runner" not in verifiers:
            cells.append("(code-free phase: test-runner dropped)")
        out.append("│    checkpoint:  " + "   ".join(cells))
    gate_cells = []
    for key, fid, label in _GATE_LABELS:
        glyph = "▣" if key in gates else "▢"
        gate_cells.append(f"{glyph} {fid} {label}")
    out.append("│    gates:  " + "   ".join(gate_cells))
    out.append("│    " + _position_line(rw.get("position")))
    return out


def _position_line(pos):
    if not pos or pos.get("phase") is None:
        return "position:  (no tasks)"
    p = pos["phase"]
    t = pos.get("task")
    s = pos.get("subtask")
    if t is None:
        loc = f"Phase {p}"
    elif s is None:
        loc = f"Phase {p} · Task {t}"
    else:
        loc = f"Phase {p} · Task {t}.{s}"
    name = pos.get("name") or ""
    return f"position:  ► {loc}" + (f"  {name}" if name else "")


def _tree(envelope):
    out = ["├─ TASK TREE " + "─" * 60]
    tree = envelope.get("task_tree") or []
    if not tree:
        out.append("│    (empty)")
        return out
    for ph in tree:
        pi = ph.get("index")
        out.append(f"│  {_marker(ph.get('status'))} Phase {pi}: {ph.get('name') or ''}")
        for tk in ph.get("tasks") or []:
            ti = tk.get("index")
            out.append("│    " + _unit_line(tk, f"{pi}.{ti}"))
            for si, sub in enumerate(tk.get("subtasks") or [], 1):
                out.append("│        " + _unit_line(sub, f"{pi}.{ti}.{si}"))
    return out


def _unit_line(unit, label):
    marker = _marker(unit.get("status", "pending"))
    bits = [f"{marker} {label} {unit.get('name') or ''}".rstrip()]
    sha = unit.get("commit_sha")
    if sha:
        bits.append(f"[{str(sha)[:7]}]")
    rc = unit.get("retry_count") or 0
    mr = unit.get("max_retries")
    if rc and mr:
        bits.append(f"◀ retry {rc}/{mr}")
    tt = unit.get("task_type")
    if tt:
        bits.append(f"[{tt}]")
    return "  ".join(bits)


def _gauges(envelope):
    q = envelope.get("quality") or {}
    cells = [f"completion {_fmt(q.get('completion_pct'))}%"]
    cov = q.get("coverage_pct")
    cells.append("coverage " + (_fmt(cov) + "%" if cov is not None else "—"))
    ac = q.get("ac_integrity")
    if ac:
        cells.append(f"AC-integrity {ac}")
    return [
        "├─ QUALITY " + "─" * 60,
        "│  " + " · ".join(cells),
        "└" + "─" * 70,
    ]


def _marker(status):
    return _MARKERS.get(status, "[ ]")


def _fmt(value):
    """Drop a trailing ``.0`` from a numeric gauge for compact display."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
