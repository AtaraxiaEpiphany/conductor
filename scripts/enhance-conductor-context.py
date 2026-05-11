#!/usr/bin/env python3
"""InstructionsLoaded hook: dynamically enhance conductor context based on loaded file.

Progressive disclosure: inject track-specific info when conductor-core.md loads.
"""

import re
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.json_utils import load_json_safe


def get_track_context(cwd: Path) -> Optional[str]:
    """Get active track context for conductor files

    Args:
        cwd: Current working directory

    Returns:
        Track context string or None
    """
    conductor_dir = cwd / "conductor"
    if not conductor_dir.exists():
        return None

    tracks_file = conductor_dir / "tracks.md"
    if not tracks_file.exists():
        return None

    try:
        content = tracks_file.read_text(encoding="utf-8")

        # Find all tracks
        tracks = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)

        active = []
        for name, path in tracks[:3]:  # Limit to 3 for context
            state_file = cwd / path / "track-state.json"
            if state_file.exists():
                state = load_json_safe(state_file)
                if state:
                    status = state.get("status", "unknown")
                    if status not in ("completed", "archived", "cancelled"):
                        phase_idx = state.get("current_phase_index", 0)
                        task_idx = state.get("current_task_index", 0)
                        active.append(f'- {name}: {status} (P{phase_idx+1}.T{task_idx+1})')

        if active:
            return "Active tracks:\n" + "\n".join(active)

    except Exception:
        pass

    return None


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    file_path = input_data.get("file_path", "")
    cwd_str = input_data.get("cwd", "")

    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    # Only enhance conductor-related files
    if "conductor" not in file_path and "conductor-core" not in file_path:
        write_simple_output()
        return

    # Get current track context if available
    track_context = get_track_context(cwd)

    # Inject enhanced context
    if track_context:
        context = f"""## Quick Reference

{track_context}

Run /conductor:status for full overview."""
        write_simple_output(additional_context=context)
    else:
        write_simple_output()


if __name__ == "__main__":
    main()