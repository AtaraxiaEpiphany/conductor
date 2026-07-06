r"""Tests for session-end.py cleanup helpers.

``has_active_tracks`` is the #7 fix: it decides whether session-end may delete
the cross-session ``session-handoff.md`` spine. It must keep the handoff
(conservatively return True) when activity is uncertain, so a parse anomaly
never silently destroys the recovery spine.
"""
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "session_end", _scripts / "session-end.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
has_active_tracks = _mod.has_active_tracks
clean_temp_files = _mod.clean_temp_files


def _registry(cwd: Path, track_rel: str):
    # ``track_rel`` is a conductor-root-relative link (e.g. ``tracks/a``); the
    # track dir lives at ``<cwd>/conductor/<track_rel>`` (the canonical layout,
    # since the registry is at ``<cwd>/conductor/tracks.md``). extract_track_dirs
    # normalizes such links to project-root-relative ``conductor/<track_rel>``,
    # so ``cwd / d`` resolves to the dir below.
    (cwd / "conductor").mkdir(parents=True, exist_ok=True)
    (cwd / "conductor" / "tracks.md").write_text(f"- [T]({track_rel})\n")
    track_dir = cwd / "conductor" / track_rel
    track_dir.mkdir(parents=True, exist_ok=True)
    return track_dir


def _state(track_dir: Path, body):
    (track_dir / "track-state.json").write_text(
        body if isinstance(body, str) else json.dumps(body))


class HasActiveTracksTests(TestCase):
    def test_in_progress_track_is_active(self):
        with tempfile.TemporaryDirectory() as d:
            td = _registry(Path(d), "tracks/a")
            _state(td, {"status": "in_progress"})
            self.assertTrue(has_active_tracks(Path(d)))

    def test_all_terminal_is_not_active(self):
        with tempfile.TemporaryDirectory() as d:
            for i, st in enumerate(("completed", "archived", "cancelled")):
                td = _registry(Path(d), f"tracks/t{i}")
                # first iteration creates the registry; later ones append
                (Path(d) / "conductor" / "tracks.md").write_text(
                    "".join(f"- [T](tracks/t{j})\n" for j in range(i + 1)))
                _state(td, {"status": st})
            self.assertFalse(has_active_tracks(Path(d)))

    def test_no_registry_is_not_active(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(has_active_tracks(Path(d)))

    def test_non_dict_state_keeps_handoff(self):
        """A state file that parses to a non-dict (here a JSON list) makes
        ``.get()`` raise inside the loop; the broad except must then keep the
        handoff (return True) rather than delete it."""
        with tempfile.TemporaryDirectory() as d:
            td = _registry(Path(d), "tracks/a")
            _state(td, "[1, 2, 3]")  # valid JSON, but not a dict
            self.assertTrue(has_active_tracks(Path(d)))


class CleanTempFilesTests(TestCase):
    def test_old_files_removed_new_kept(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            old = tmp / "old.txt"
            new = tmp / "new.txt"
            old.write_text("x")
            new.write_text("y")
            # Backdate old.txt by 48h.
            ts = datetime.now(timezone.utc) - timedelta(hours=48)
            import os
            os.utime(old, (ts.timestamp(), ts.timestamp()))
            cleaned = clean_temp_files(tmp, max_age_hours=24)
            self.assertEqual(cleaned, 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())

    def test_missing_dir_is_noop(self):
        self.assertEqual(clean_temp_files(Path("/nonexistent/zzz")), 0)


if __name__ == "__main__":
    main()
