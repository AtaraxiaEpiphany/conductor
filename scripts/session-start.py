#!/usr/bin/env python3
"""SessionStart hook: inject runtime/core-contract.md + session handoff into session context.

On compact events, inject a compact summary to reduce context pressure.
"""

import json
import os
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.env import get_data_dir


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

    # Output
    write_simple_output(additional_context=full_content)


if __name__ == "__main__":
    main()