"""Pins the post-compaction re-orientation directive in COMPACT_CONTENT.

On a `compact` SessionStart, the ONLY conductor content injected is the
COMPACT_CONTENT stub (session-start.py get_conductor_content short-circuits
advisory scans + the full core-contract for source=="compact"). After a lossy
compaction the model's reliable knowledge is what's on disk, so the stub must
make the first action deterministic: re-orient via `track-state recover` rather
than relying on compressed memory. This pins that the directive is present and
names the command, so a future edit can't silently drop it.
"""
import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Hyphenated module name — load by path with scripts/ on sys.path (for lib.*).
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "session_start_compact_recover", str(SCRIPTS / "session-start.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
COMPACT_CONTENT = _mod.COMPACT_CONTENT


class CompactRecoverDirectiveTests(TestCase):
    def test_directive_names_the_recover_command(self):
        self.assertIn("track-state recover", COMPACT_CONTENT)

    def test_directive_leads_with_re_orientation(self):
        # The directive must precede the Task State table so it reads as step 1.
        i_directive = COMPACT_CONTENT.find("track-state recover")
        i_state = COMPACT_CONTENT.find("Task State:")
        self.assertGreater(i_directive, -1)
        self.assertGreater(i_state, i_directive)

    def test_directive_forbids_memory_reliance(self):
        # "never rely on memory" is the contract that makes disk the source of truth.
        self.assertIn("never rely on memory", COMPACT_CONTENT)


if __name__ == "__main__":
    main()
