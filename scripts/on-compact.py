#!/usr/bin/env python3
"""on-compact - PreCompact hook.

Injects compression priority instructions to preserve dispatch loop state.
"""

import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import write_simple_output


COMPRESSION_INSTRUCTIONS = """COMPRESSION PRIORITY:
[KEEP] Sections 3.0-3.7 (active dispatch loop) + last track-state output + last subagent result
[COMPRESS] All completed task results to: task_name=sha,status (one line each)
[DISCARD] Sections 1.0-2.0 (one-time setup, re-read from disk if needed)
[DISCARD] All intermediate CLI outputs (lock, sync-plan, phase-done details)
[DISCARD] Section 4.0 post-loop (re-read from the plugin template when needed)

CRITICAL: After compression, re-read ${CLAUDE_PLUGIN_ROOT}/templates/post-loop.md only when entering Section 4.0."""


def main():
    """Main hook function"""
    write_simple_output(additional_context=COMPRESSION_INSTRUCTIONS)


if __name__ == "__main__":
    main()