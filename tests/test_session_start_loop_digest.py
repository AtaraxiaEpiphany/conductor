r"""Tests for the SessionStart comprehension-debt nudge (get_loop_digest).

get_loop_digest re-surfaces the latest ACTIVE track's Critical/High review
findings so the operator reads what the loop shipped — the recurring counter-
measure to Osmani's comprehension debt (the post-loop §7.5 digest fires once
per track; this fires on every non-compact SessionStart). Advisory only: it
returns '' whenever there's nothing worth surfacing and never raises, so a
malformed review/state file can't break session bootstrap. These tests pin the
resolution (glob + terminal-filter-before-newest-mtime), the severity filter
(case-insensitive, Critical/High only), the output shape, and the defensive
guards.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Hyphenated module name — load by path with scripts/ on sys.path (for lib.*).
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "session_start_ld", str(SCRIPTS / "session-start.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
get_loop_digest = _mod.get_loop_digest

_NUDGE = "comprehension debt"


def _track_dir(root: Path, track_id: str) -> Path:
    return root / "conductor" / "tracks" / track_id


def _seed_track(root, track_id, findings=None, status="in_progress",
                review_raw=None, mtime=None):
    """Seed a track with track-state.json (at track-dir root) + a review file.

    review_raw lets a test write arbitrary bytes (malformed JSON / non-list
    findings). Otherwise the findings list is serialized as {"findings": [...]}.
    """
    tdir = _track_dir(root, track_id)
    cond = tdir / ".conductor"
    cond.mkdir(parents=True)
    (tdir / "track-state.json").write_text(json.dumps({"status": status}))
    review = cond / "review-result.json"
    review.write_text(review_raw if review_raw is not None
                      else json.dumps({"findings": findings or []}))
    if mtime is not None:
        os.utime(review, (mtime, mtime))
    return review


def _finding(severity, title="t", file="a.py", lines="L1-L2"):
    return {"severity": severity, "title": title, "file": file, "lines": lines}


class EmptyReturnTests(TestCase):
    def test_no_tracks_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(get_loop_digest(root), "")

    def test_no_review_file_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", findings=[_finding("High")])
        # Remove the review file but keep the track.
        (_track_dir(root, "t1_20260629") / ".conductor" /
         "review-result.json").unlink()
        self.assertEqual(get_loop_digest(root), "")

    def test_only_medium_low_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629",
                    findings=[_finding("Medium"), _finding("Low")])
        self.assertEqual(get_loop_digest(root), "")

    def test_malformed_json_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", review_raw="{ not valid json")
        self.assertEqual(get_loop_digest(root), "")

    def test_findings_not_list_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629",
                    review_raw=json.dumps({"findings": {"a": 1}}))
        self.assertEqual(get_loop_digest(root), "")


class SurfacingTests(TestCase):
    def test_critical_high_surfaced(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "auth_login_20260629", findings=[
            _finding("Critical", title="Null deref", file="auth.py", lines="L10-L12"),
            _finding("High", title="SQL injection", file="db.py", lines="L40"),
        ])
        out = get_loop_digest(root)
        self.assertIn("Loop digest", out)
        self.assertIn("auth_login_20260629", out)      # track id
        self.assertIn("Null deref", out)
        self.assertIn("SQL injection", out)
        self.assertIn("auth.py:L10-L12", out)
        self.assertIn(_NUDGE, out)                      # the nudge sentence
        self.assertIn("review-result.json", out)        # drill-down path

    def test_severity_case_insensitive(self):
        # The review file is LLM-written — severity casing can drift.
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", findings=[
            _finding("critical", title="c"),
            _finding("HIGH", title="h"),
        ])
        out = get_loop_digest(root)
        self.assertIn("c", out)
        self.assertIn("h", out)

    def test_capped_at_three_with_more_count(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629",
                    findings=[_finding("High", title=f"f{i}") for i in range(5)])
        out = get_loop_digest(root)
        self.assertIn("f0", out)
        self.assertIn("f2", out)
        self.assertNotIn("f3", out)       # only first 3
        self.assertIn("(+2 more)", out)

    def test_missing_severity_skipped(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", findings=[
            {"title": "no-severity", "file": "x.py"},   # no severity → skipped
            _finding("High", title="kept"),
        ])
        out = get_loop_digest(root)
        self.assertNotIn("no-severity", out)
        self.assertIn("kept", out)

    def test_untitled_finding_renders_placeholder(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629",
                    findings=[{"severity": "High"}])   # no title
        out = get_loop_digest(root)
        self.assertIn("(untitled)", out)


class TerminalStatusTests(TestCase):
    """archived/cancelled are filtered BEFORE newest-mtime so a done track can
    neither nag forever nor shadow an active one. completed is NOT terminal."""

    def test_archived_track_skipped(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", status="archived",
                    findings=[_finding("Critical", title="c")])
        self.assertEqual(get_loop_digest(root), "")

    def test_cancelled_track_skipped(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", status="cancelled",
                    findings=[_finding("Critical", title="c")])
        self.assertEqual(get_loop_digest(root), "")

    def test_completed_track_not_skipped(self):
        # Finalized-but-not-archived still carries unread risk → still nudges.
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", status="completed",
                    findings=[_finding("Critical", title="c")])
        self.assertIn("c", get_loop_digest(root))

    def test_track_state_missing_still_surfaces(self):
        # No track-state.json → can't prove terminal → don't block (surface).
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "t1_20260629", findings=[_finding("High", title="h")])
        (_track_dir(root, "t1_20260629") / "track-state.json").unlink()
        self.assertIn("h", get_loop_digest(root))


class NewestActiveWinsTests(TestCase):
    def test_multiple_active_newest_wins(self):
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "older_20260601",
                    findings=[_finding("High", title="old")], mtime=1_000_000)
        _seed_track(root, "newer_20260629",
                    findings=[_finding("High", title="new")], mtime=2_000_000)
        out = get_loop_digest(root)
        self.assertIn("newer_20260629", out)
        self.assertIn("new", out)
        self.assertNotIn("old", out)

    def test_newest_is_archived_falls_through_to_older_active(self):
        # The risk this guards: an archived track with the freshest mtime would
        # suppress the nudge for an older active track if we picked newest first.
        root = Path(tempfile.mkdtemp())
        _seed_track(root, "active_20260601",
                    findings=[_finding("High", title="active-hit")], mtime=1_000_000)
        _seed_track(root, "done_20260629", status="archived",
                    findings=[_finding("Critical", title="archived-hit")], mtime=2_000_000)
        out = get_loop_digest(root)
        self.assertIn("active-hit", out)
        self.assertNotIn("archived-hit", out)


if __name__ == "__main__":
    main()
