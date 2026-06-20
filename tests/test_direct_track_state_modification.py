"""Tests for pre-command-check.is_direct_track_state_modification.

Regression: the rm/mv/git-rm, sed, and python patterns used ``.*`` between the
verb and ``track-state``, so the match spanned shell separators. A compound
command like ``rm -f unrelated.tmp; git diff .../track-state.json`` matched
because ``rm`` and ``track-state.json`` appeared in sequence — flagging a
read-only diff as a direct state-file modification. The patterns now bound the
verb-to-path gap to a single command segment.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
# Production gets scripts/ on sys.path automatically (script dir = sys.path[0]);
# replicate that so the module's `from lib.hook_io import …` resolves.
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))
# Hyphenated module — load by path (matches the repo's hook-test convention).
_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
is_direct = _mod.is_direct_track_state_modification


class IsDirectTrackStateModificationTests(TestCase):
    # --- True positives: real direct modifications ---
    def test_rm(self):
        self.assertTrue(is_direct("rm track-state.json"))

    def test_rm_with_flags(self):
        self.assertTrue(is_direct("rm -f track-state.json"))
        self.assertTrue(is_direct("rm -rf conductor/tracks/x/track-state.json"))

    def test_mv(self):
        self.assertTrue(is_direct("mv track-state.json track-state.json.bak"))

    def test_git_rm(self):
        self.assertTrue(is_direct("git rm conductor/tracks/x/track-state.json"))
        self.assertTrue(is_direct("git rm -f conductor/tracks/x/track-state.json"))

    def test_sed_inplace(self):
        self.assertTrue(is_direct("sed -i 's/a/b/' track-state.json"))

    def test_python_write(self):
        self.assertTrue(is_direct(
            "python3 -c \"open('track-state.json','w').write('{}')\""))

    def test_rm_same_segment_with_trailing_command(self):
        # rm and track-state.json in the SAME segment still matches even when a
        # later segment does something else.
        self.assertTrue(is_direct("rm track-state.json && git status"))

    # --- THE REGRESSION: a destructive verb in a DIFFERENT segment than the
    #     read-only track-state.json reference must NOT match ---
    def test_rm_unrelated_then_diff_track_state(self):
        self.assertFalse(is_direct(
            "rm -f .pytest_capture.txt; "
            "git diff conductor/tracks/office-cli_20260618/track-state.json"))

    def test_rm_unrelated_then_diff_short(self):
        self.assertFalse(is_direct("rm other.tmp && git diff track-state.json"))

    def test_pure_git_diff(self):
        self.assertFalse(is_direct("git diff conductor/tracks/x/track-state.json"))

    def test_read_only_ops(self):
        for cmd in ("cat track-state.json",
                    "git status track-state.json",
                    "ls track-state.json",
                    "less track-state.json"):
            self.assertFalse(is_direct(cmd), f"{cmd!r} should not be flagged")

    def test_sed_unrelated_then_cat_track_state(self):
        # sed editing a DIFFERENT file; track-state only read by cat in another
        # segment — must not match.
        self.assertFalse(is_direct("sed -i 's/a/b/' other.json; cat track-state.json"))


class RobustSegmentationTests(TestCase):
    """Shell-aware segmentation: a separator only splits at the top level.

    These are the cases the regex char-class approach ([^;&|\\n]*) could not
    handle — a quoted/substituted separator must NOT split (false negative),
    while cross-segment co-occurrence still must not match (false positive).
    """

    # --- Quoted/substituted separators stay in-segment (no false negative) ---
    def test_double_quoted_separator_keeps_segment(self):
        self.assertTrue(is_direct('rm "a;b" track-state.json'))
        self.assertTrue(is_direct('rm "a|b" track-state.json'))

    def test_single_quoted_separator_keeps_segment(self):
        self.assertTrue(is_direct("rm 'a;b' track-state.json"))

    def test_quoted_separator_in_readonly_command_not_flagged(self):
        # git log with a quoted ";" in --format plus a track-state path: read-only.
        self.assertFalse(is_direct('git log --format="%h;%s" -- track-state.json'))

    def test_subshell_separator_does_not_split(self):
        # ';' inside $(...) is not top-level; the rm segment still holds track-state.
        self.assertTrue(is_direct('rm "$(echo track-state.json)"'))
        # A subshell separator before the real command must not hide a later rm.
        self.assertTrue(is_direct('echo $(date; uptime) && rm track-state.json'))

    def test_redirect_fd_not_treated_as_separator(self):
        # '2>&1' must not split; the resulting segment has no destructive verb.
        self.assertFalse(is_direct('git log 2>&1 | grep track-state.json'))

    def test_newline_separates(self):
        self.assertFalse(is_direct("rm other.tmp\ngit diff track-state.json"))
        self.assertTrue(is_direct("echo ok\nrm track-state.json"))

    # --- Cross-segment false positives stay fixed under the new segmenter ---
    def test_compound_with_pipe(self):
        self.assertFalse(is_direct("rm other.tmp | git diff track-state.json"))


class PreCommandCheckEndToEndTests(TestCase):
    """Run the real hook via subprocess; assert the permission decision."""

    def _decision(self, command: str):
        # A fresh temp dir has no conductor registry, so only the ungated
        # is_direct_track_state_modification check applies for these commands.
        cwd = tempfile.mkdtemp()
        payload = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": cwd,
        })
        r = subprocess.run(
            [sys.executable, str(_scripts / "pre-command-check.py")],
            input=payload, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, r.returncode, r.stderr)
        return json.loads(r.stdout).get("hookSpecificOutput", {}).get("permissionDecision")

    def test_real_rm_is_asked(self):
        self.assertEqual("ask", self._decision("rm track-state.json"))

    def test_rm_unrelated_then_diff_is_allowed(self):
        # THE BYPASS: previously asked; now allowed.
        self.assertIsNone(self._decision(
            "rm -f .pytest_capture.txt; "
            "git diff conductor/tracks/office-cli_20260618/track-state.json"))


if __name__ == "__main__":
    main()
