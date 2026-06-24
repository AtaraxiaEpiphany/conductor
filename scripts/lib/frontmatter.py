"""YAML frontmatter provenance for the wiki corpus (O4).

Scoped corpus docs (``conductor/design/``, ``conductor/resource/``,
``conductor/requirement/``, ``conductor/queries/``) carry provenance frontmatter
so freshness/staleness checks are **evidence-based** rather than heuristic — the
harness-engineering "lint errors include remediation" principle applied to docs.

Exempt (auto-owned synthesis/navigation, regenerated wholesale — frontmatter
would only churn): ``overview.md``, ``purpose.md``, ``log.md``, ``index.md``,
and any ``index.md`` scoped entry point.

Stdlib-only: a minimal tolerant parser for the field shapes we actually emit
(``type:`` scalar, ``sources:`` list, ``last_verified:`` scalar, plus the
query-page ``topic``/``created`` scalars). Not a general YAML parser.
"""
from pathlib import Path
from typing import Dict, List, Optional

# Required provenance fields on a scoped corpus doc.
REQUIRED_FM_FIELDS = ("type", "sources", "last_verified")

# Doc basenames exempt from the frontmatter requirement (auto-owned / navigation).
_EXEMPT_BASENAMES = {"overview.md", "purpose.md", "log.md", "index.md"}

# Corpus subdirectories whose docs MUST carry provenance frontmatter.
CORPUS_PROVENANCE_DIRS = ("design", "resource", "requirement", "queries")


def parse_frontmatter(text: str) -> Optional[Dict[str, object]]:
    """Parse a leading YAML frontmatter block.

    Returns ``None`` when the document has no frontmatter fence. Tolerant of
    unknown keys; coerces ``sources``/list-valued keys to lists and scalars to
    stripped strings.
    """
    if not text.startswith("---"):
        return None
    # Split on line boundaries to find the closing fence.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return None

    fm: Dict[str, object] = {}
    key: Optional[str] = None
    for line in lines[1:close]:
        if not line.strip():
            continue
        # List item under the current key.
        if line.startswith("  - ") or line.startswith("- "):
            if key is None:
                continue
            item = line.split("- ", 1)[1].strip()
            cur = fm.get(key)
            if isinstance(cur, list):
                cur.append(item)
            else:
                fm[key] = [item]
            continue
        # key: value
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v == "":  # list-valued or empty; default to list for known list keys
                fm[k] = [] if k == "sources" else ""
            elif v.startswith("[") and v.endswith("]"):
                # Inline list form: `sources: [a, b]` or `sources: []`
                inner = v[1:-1].strip()
                fm[k] = [x.strip() for x in inner.split(",")] if inner else []
            else:
                fm[k] = v
            key = k
    return fm


def has_frontmatter(text: str) -> bool:
    return parse_frontmatter(text) is not None


def missing_required_fields(text: str) -> List[str]:
    """Return the subset of REQUIRED_FM_FIELDS absent from the frontmatter.

    Empty list = compliant (or no frontmatter required → caller should check
    ``is_exempt`` first).
    """
    fm = parse_frontmatter(text)
    if fm is None:
        return list(REQUIRED_FM_FIELDS)
    missing = []
    for field in REQUIRED_FM_FIELDS:
        if field not in fm:
            missing.append(field)
        elif field == "sources" and not fm[field]:
            missing.append(field)  # present but empty list = no provenance
    return missing


def is_exempt(doc_path: Path) -> bool:
    """Auto-owned synthesis/navigation docs are exempt from provenance frontmatter."""
    return doc_path.name in _EXEMPT_BASENAMES


def is_corpus_doc(doc_path: Path, conductor_dir: Path) -> bool:
    """A scoped corpus doc: under a provenance-bearing subdir of conductor/."""
    try:
        rel = doc_path.relative_to(conductor_dir)
    except ValueError:
        return False
    if not rel.parts:
        return False
    return rel.parts[0] in CORPUS_PROVENANCE_DIRS


def check_corpus_frontmatter(conductor_dir: Path) -> List[Dict[str, str]]:
    """Scan the corpus for scoped docs missing required provenance frontmatter.

    Returns a list of findings, each ``{"file": <rel path>, "missing": <fields>}``.
    Exempt basenames (incl. scoped index.md entry points) are skipped.
    """
    findings: List[Dict[str, str]] = []
    if not conductor_dir.is_dir():
        return findings
    for sub in CORPUS_PROVENANCE_DIRS:
        d = conductor_dir / sub
        if not d.is_dir():
            continue
        for md in sorted(d.rglob("*.md")):
            if is_exempt(md):
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            missing = missing_required_fields(text)
            if missing:
                findings.append({
                    "file": str(md.relative_to(conductor_dir)),
                    "missing": ", ".join(missing),
                })
    return findings
