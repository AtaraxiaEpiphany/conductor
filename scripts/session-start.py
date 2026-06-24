#!/usr/bin/env python3
"""SessionStart hook: inject runtime/core-contract.md + session handoff into session context.

On compact events, inject a compact summary to reduce context pressure.
"""

import re
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.env import get_data_dir
from lib.path_utils import get_file_age_hours
from lib.frontmatter import check_corpus_frontmatter


COMPACT_CONTENT = """## Conductor Core (compact)

Task State: | [ ] pending | [~] in_progress | [x] completed [sha] | [!] failed [sha] | [>] skipped [sha] | [d] deferred [sha] | [#] blocked [sha] | [-] cancelled [sha] |

Commit: <type>(<scope>): <description>

Firewall: F1(state lock) F2(TDD) F3(coverage) F4(SHA) F5(checkpoint) F6(context guard)
Anti-patterns: V1-V11. Violation -> STOP -> WORKFLOW VIOLATION: <code> -> revert."""


def get_session_handoff(data_dir: Path) -> str:
    """Get session handoff content from previous session

    Args:
        data_dir: Data directory path

    Returns:
        Handoff content or empty string
    """
    handoff_file = data_dir / "session-handoff.md"
    if handoff_file.exists():
        try:
            return f"\n\n--- Previous Session Handoff ---\n{handoff_file.read_text(encoding='utf-8')}"
        except Exception:
            pass
    return ""


def get_conductor_content(plugin_root: Path, source: str) -> str:
    """Get conductor content based on source type

    Args:
        plugin_root: Plugin root directory
        source: Source type (startup or compact)

    Returns:
        Content to inject
    """
    if source == "compact":
        return COMPACT_CONTENT

    # Load full runtime/core-contract.md
    instructions_file = plugin_root / "runtime" / "core-contract.md"
    if instructions_file.exists():
        try:
            return instructions_file.read_text(encoding="utf-8")
        except Exception:
            pass

    return COMPACT_CONTENT


_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
# Bound the scan so a very large corpus can't slow session start unboundedly.
_MAX_DOCS_FOR_ORPHAN_SCAN = 500


def _count_broken_wikilinks(file_path: Path, project_root: Path) -> int:
    """Count [[wikilinks]] in a single file that don't resolve (append .md, project-relative)."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return 0
    broken = 0
    for target in _WIKILINK.findall(text):
        # Resolution rule (core contract): path relative to project root, append .md.
        candidate = project_root / (target + ".md")
        if not candidate.exists():
            # Also try the raw target (some links already include an extension/anchor).
            if not (project_root / target).exists():
                broken += 1
    return broken


def get_wiki_drift_warnings(project_root: Path) -> str:
    """Cheap, high-signal wiki-drift scan run at session start (GC pillar).

    Advisory only — surfaced as additional_context, never blocks. Three signals:
    overview.md staleness (>30d), scoped docs missing provenance frontmatter,
    and broken [[wikilinks]] in overview.md (the auto-owned synthesis that must
    never reference a non-existent doc). Returns '' when the wiki is absent or clean.
    """
    conductor = project_root / "conductor"
    if not conductor.is_dir():
        return ""

    warnings = []

    # 1. overview.md staleness (regenerated each track via doc-syncer Phase 2).
    overview = conductor / "overview.md"
    age_h = get_file_age_hours(overview)
    if age_h is not None and age_h > 30 * 24:
        warnings.append(
            f"overview.md is {int(age_h // 24)}d stale — run /conductor:implement on a "
            f"track (or /conductor:wiki ingest) to regenerate it."
        )

    # 2. scoped docs missing provenance frontmatter (evidence-based drift, O4).
    fm_findings = check_corpus_frontmatter(conductor)
    if fm_findings:
        warnings.append(
            f"{len(fm_findings)} scoped doc(s) missing provenance frontmatter "
            f"(type/sources/last_verified) — run /conductor:wiki-doctor lint."
        )

    # 3. broken [[wikilinks]] in overview.md (auto-owned; must never dangle).
    try:
        doc_count = sum(1 for _ in conductor.rglob("*.md"))
    except Exception:
        doc_count = 0
    if doc_count <= _MAX_DOCS_FOR_ORPHAN_SCAN and overview.exists():
        broken = _count_broken_wikilinks(overview, project_root)
        if broken:
            warnings.append(
                f"{broken} broken [[wikilink]](s) in overview.md — run "
                f"/conductor:wiki-doctor lint."
            )

    if not warnings:
        return ""
    body = "\n".join(f"- {w}" for w in warnings)
    return f"\n\n--- Wiki drift (advisory GC) ---\n{body}\n"


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    source = input_data.get("source", "startup")

    # Get paths
    plugin_root = Path(__file__).parent.parent
    data_dir = get_data_dir()

    # Get content
    content = get_conductor_content(plugin_root, source)

    # Add session handoff if exists
    handoff = get_session_handoff(data_dir)
    full_content = content + handoff

    # Wiki drift GC (advisory) — skip on compact (keep that context minimal).
    if source != "compact":
        cwd_str = input_data.get("cwd", "")
        project_root = Path(cwd_str) if cwd_str else Path.cwd()
        full_content += get_wiki_drift_warnings(project_root)

    # Output
    write_simple_output(additional_context=full_content)


if __name__ == "__main__":
    main()