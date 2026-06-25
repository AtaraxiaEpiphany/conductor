"""Wiki health metrics — read-only computation for ``wiki-status``.

Deterministic metrics that the ``wiki status`` skill used to gather inline
(Glob/Grep/Read + ad-hoc parsing). Centralizing them here keeps the skill body
a thin reporter: it calls ``wiki-status <project-dir>`` and renders the JSON.
Mirrors the ``track-state`` pattern (script computes, skill renders).
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path


_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_LOG_SEP_CELL = set("-: ")


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def cmd_status(project_dir):
    """Compute wiki health metrics (read-only). Emits JSON, exits 0.

    Callers switch on ``status``:
    - ``infra_missing`` → ``overview.md`` or ``log.md`` absent (caller halts).
    - ``ok``            → full metrics payload.
    """
    root = Path(project_dir).resolve()
    cond = root / "conductor"
    overview = cond / "overview.md"
    log = cond / "log.md"

    missing = [str(p.relative_to(root)) for p in (overview, log) if not p.exists()]
    if missing:
        out(dict(status="infra_missing", missing=missing))
        return

    out(dict(
        status="ok",
        document_count=_doc_count(cond),
        log=_log_metrics(log),
        overview=_overview_freshness(overview),
        orphan_scan=_orphan_scan(cond, root),
        tracks=_track_summary(cond),
    ))


def _wiki_docs(cond):
    """All ``conductor/**/*.md`` except the ``tracks/`` subtree (track artifacts)."""
    docs = []
    if not cond.is_dir():
        return docs
    for p in cond.rglob("*.md"):
        try:
            rel = p.relative_to(cond)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "tracks":
            continue
        docs.append(p)
    return docs


def _doc_count(cond):
    return len(_wiki_docs(cond))


def _log_metrics(log_path):
    """Parse the pipe-delimited log table.

    Schema (templates/wiki-log.md): ``| Timestamp | Track | Operation | Files | Summary |``.
    Returns ``{entries, last_timestamp, last_summary}``. Empty/absent → entries=0.
    """
    try:
        text = log_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return dict(entries=0, last_timestamp=None, last_summary=None)

    data_rows = []
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|") and len(s) > 2):
            continue
        cells = [c.strip() for c in s[1:-1].split("|")]
        if not any(cells):
            continue
        if all(set(c) <= _LOG_SEP_CELL for c in cells):  # separator: |---|---|
            continue
        if any("timestamp" in c.lower() for c in cells):  # header row
            continue
        data_rows.append(cells)

    if not data_rows:
        return dict(entries=0, last_timestamp=None, last_summary=None)
    last = data_rows[-1]
    timestamp = last[0] if last else None
    summary = next((c for c in reversed(last) if c), None) if last else None
    return dict(entries=len(data_rows), last_timestamp=timestamp, last_summary=summary)


def _overview_freshness(overview_path):
    """Classify ``overview.md`` staleness from its ``> Last updated: <ISO>`` line."""
    try:
        text = overview_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return dict(timestamp=None, classification="outdated")

    m = re.search(r"Last updated:\s*(\S+)", text)
    ts = m.group(1) if m else None
    if not ts:
        return dict(timestamp=None, classification="outdated")
    return dict(timestamp=ts, classification=_classify_freshness(ts))


def _classify_freshness(ts):
    """<=7d fresh, <=30d stale, else outdated. Unparseable → outdated."""
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "outdated"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - when).total_seconds() / 86400
    if age_days < 0:
        return "fresh"  # clock skewed forward; treat as fresh
    if age_days <= 7:
        return "fresh"
    if age_days <= 30:
        return "stale"
    return "outdated"


def _resolve_target(target, cond, root):
    """Resolve a ``[[wikilink]]`` target to an existing file path, or None.

    Targets are usually stored without the ``.md`` suffix (e.g. ``conductor/overview``).
    We try with/without ``.md``, relative to both repo root and ``conductor/``.
    """
    t = re.split(r"[|#]", target.strip())[0].strip()  # drop [[page|alias]] / [[page#anchor]]
    if not t:
        return None
    for base in (root, cond):
        p = base / t
        if p.is_file():
            return p
        if not t.endswith(".md"):
            if (base / (t + ".md")).is_file():
                return base / (t + ".md")
    return None


def _orphan_scan(cond, root):
    """Find ``[[wikilinks]]`` pointing to non-existent files.

    Scans all ``conductor/**/*.md`` (including ``tracks/``, per the wiki spec).
    Returns ``{broken_count, broken_targets[], in_files}``.
    """
    if not cond.is_dir():
        return dict(broken_count=0, broken_targets=[], in_files=0)

    broken = {}  # target -> set of source relpaths
    for src in cond.rglob("*.md"):
        try:
            text = src.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, UnicodeDecodeError):
            continue
        for m in _WIKILINK.finditer(text):
            target = m.group(1)
            if _resolve_target(target, cond, root) is None:
                rel = str(src.relative_to(root))
                broken.setdefault(target, set()).add(rel)

    in_files = len({f for files in broken.values() for f in files})
    return dict(
        broken_count=len(broken),
        broken_targets=sorted(broken.keys()),
        in_files=in_files,
    )


def _track_summary(cond):
    """Count status markers in ``conductor/tracks.md``."""
    try:
        text = (cond / "tracks.md").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return dict(completed=0, in_progress=0, new=0)

    return dict(
        completed=len(re.findall(r"\[x\]", text)),
        in_progress=len(re.findall(r"\[~\]", text)),
        new=len(re.findall(r"\[ \]", text)),
        failed=len(re.findall(r"\[!\]", text)),
        skipped=len(re.findall(r"\[>\]", text)),
        deferred=len(re.findall(r"\[d\]", text)),
        blocked=len(re.findall(r"\[#\]", text)),
    )
