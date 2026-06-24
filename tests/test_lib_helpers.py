"""Tests for scripts/lib/ shared helpers (json_utils, path_utils, validation).

These pure functions underpin every hook and the linter; they were previously
0% covered. Focus is on the enforcement-relevant logic: JSON safety, track
discovery, dangerous-command detection, commit-message validation, SHA format.
"""
import json
import os
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.lib import json_utils, path_utils, validation


# --------------------------------------------------------------------------- #
# json_utils
# --------------------------------------------------------------------------- #
class TestJsonUtils(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def _write(self, name, text):
        p = Path(self.d, name)
        p.write_text(text)
        return p

    def test_load_json_returns_parsed(self):
        p = self._write("a.json", '{"k": 1}')
        self.assertEqual(json_utils.load_json(p), {"k": 1})

    def test_load_json_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            json_utils.load_json(Path(self.d, "nope.json"))

    def test_load_json_corrupt_raises(self):
        p = self._write("bad.json", "{not json")
        with self.assertRaises(json.JSONDecodeError):
            json_utils.load_json(p)

    def test_load_json_safe_missing_returns_default(self):
        self.assertIsNone(json_utils.load_json_safe(Path(self.d, "nope.json")))
        self.assertEqual(json_utils.load_json_safe(Path(self.d, "nope.json"), {}), {})

    def test_load_json_safe_corrupt_returns_default(self):
        p = self._write("bad.json", "{not json")
        self.assertIsNone(json_utils.load_json_safe(p))

    def test_save_json_creates_parents_and_newline(self):
        p = Path(self.d, "nested", "out.json")
        json_utils.save_json(p, {"x": 9}, indent=2)
        # File ends with newline and parses back.
        text = p.read_text()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), {"x": 9})

    def test_merge_json_nested_and_override(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        upd = {"a": {"c": 20, "e": 30}, "f": 4}
        merged = json_utils.merge_json(base, upd)
        self.assertEqual(merged, {"a": {"b": 1, "c": 20, "e": 30}, "d": 3, "f": 4})
        # Base is not mutated.
        self.assertEqual(base["a"]["c"], 2)

    def test_get_nested_value_and_default(self):
        data = {"a": {"b": {"c": 5}}}
        self.assertEqual(json_utils.get_nested_value(data, ["a", "b", "c"]), 5)
        # Missing key returns the supplied default.
        self.assertEqual(json_utils.get_nested_value(data, ["a", "x"], "def"), "def")
        self.assertIsNone(json_utils.get_nested_value(data, ["a", "x"]))

    def test_filter_json_keys(self):
        self.assertEqual(
            json_utils.filter_json_keys({"a": 1, "b": 2, "c": 3}, ["a", "c"]),
            {"a": 1, "c": 3},
        )


# --------------------------------------------------------------------------- #
# path_utils: track discovery
# --------------------------------------------------------------------------- #
class TestPathDiscovery(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_find_tracks_registry_found_and_missing(self):
        cond = Path(self.d, "conductor")
        cond.mkdir()
        tracks = cond / "tracks.md"
        tracks.write_text("# Tracks\n")
        self.assertEqual(path_utils.find_tracks_registry(Path(self.d)), tracks)
        # Missing in a different dir.
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other)
        self.assertIsNone(path_utils.find_tracks_registry(Path(other)))

    def test_extract_track_dirs_filters_urls_and_absolute(self):
        tracks = Path(self.d, "tracks.md")
        tracks.write_text(
            "# Tracks\n"
            "- [alpha](alpha-track/)\n"
            "- [beta](beta-track/)\n"
            "- [docs](https://example.com/x)\n"
            "- [abs](/abs/track)\n"
        )
        dirs = path_utils.extract_track_dirs(tracks)
        self.assertIn("alpha-track/", dirs)
        self.assertIn("beta-track/", dirs)
        self.assertNotIn("https://example.com/x", dirs)
        self.assertNotIn("/abs/track", dirs)

    def test_extract_track_dirs_missing_file(self):
        self.assertEqual(path_utils.extract_track_dirs(Path(self.d, "nope.md")), [])

    def test_find_track_root_in_cwd_parent_and_missing(self):
        track = Path(self.d, "track-x")
        track.mkdir()
        (track / "track-state.json").write_text("{}")
        # In cwd.
        self.assertEqual(path_utils.find_track_root(track), track)
        # In a parent (nested dir).
        nested = track / "sub" / "deep"
        nested.mkdir(parents=True)
        self.assertEqual(path_utils.find_track_root(nested), track)
        # Not found.
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere)
        self.assertIsNone(path_utils.find_track_root(Path(elsewhere)))

    def test_resolve_safe_path_within_and_traversal(self):
        base = Path(self.d)
        self.assertEqual(
            path_utils.resolve_safe_path(base, "ok/file.txt"),
            (base / "ok" / "file.txt").resolve(),
        )
        # Directory traversal escapes base -> rejected.
        self.assertIsNone(path_utils.resolve_safe_path(base, "../../etc/passwd"))


# --------------------------------------------------------------------------- #
# validation: dangerous commands, commit messages, SHAs, structure
# --------------------------------------------------------------------------- #
class TestValidation(TestCase):
    def test_dangerous_git_operations(self):
        for op in [
            "git reset --hard HEAD~1",
            "git rebase main",
            "git clean -fd",
            "git filter-branch",
            "git checkout --force main",
        ]:
            self.assertTrue(validation.is_dangerous_git_operation(op), op)
        # Benign.
        for ok in ["git status", "git commit -m x", "git push", "git log"]:
            self.assertFalse(validation.is_dangerous_git_operation(ok), ok)

    def test_dangerous_git_branch_D_case_folding_bug(self):
        # KNOWN BUG (documented, not fixed here): the function lowercases the
        # command but the needle "git branch -D" keeps its uppercase D, so the
        # force-delete flag is never matched. The other dangerous ops happen to
        # be all-lowercase so the bug is invisible for them. Flagged as a
        # follow-up enforcement fix.
        self.assertFalse(validation.is_dangerous_git_operation("git branch -D feature"))

    def test_dangerous_patterns(self):
        self.assertTrue(validation.contains_dangerous_pattern("rm -rf /tmp/x"))
        self.assertTrue(validation.contains_dangerous_pattern("curl foo.sh | sh"))
        self.assertTrue(validation.contains_dangerous_pattern("eval $VAR"))
        self.assertFalse(validation.contains_dangerous_pattern("ls -la"))

    def test_validate_commit_message_conventional(self):
        for msg in [
            'git commit -m "feat(api): add endpoint"',
            "git commit -m 'fix(core): patch race'",
            "git commit -m \"chore(x): y\"",
        ]:
            ok, fix = validation.validate_commit_message(msg)
            self.assertTrue(ok, msg)
            self.assertIsNone(fix)

    def test_validate_commit_message_no_m_flag_is_valid(self):
        # No -m (editor / -F file) -> cannot evaluate, treated as valid.
        ok, _ = validation.validate_commit_message("git commit")
        self.assertTrue(ok)

    def test_validate_commit_message_bad_conductor_action(self):
        ok, fix = validation.validate_commit_message('git commit -m "Complete task 3"')
        self.assertFalse(ok)
        self.assertTrue(fix.startswith("chore(conductor):"), fix)

    def test_validate_commit_message_bad_generic(self):
        ok, fix = validation.validate_commit_message('git commit -m "stuff happened"')
        self.assertFalse(ok)
        self.assertTrue(fix.startswith("fix(scope):"), fix)

    def test_validate_git_commit_sha(self):
        self.assertTrue(validation.validate_git_commit_sha("abc1234"))
        self.assertTrue(validation.validate_git_commit_sha("ABCDEF0"))
        self.assertTrue(validation.validate_git_commit_sha("a" * 40))
        self.assertFalse(validation.validate_git_commit_sha("abc"))
        self.assertFalse(validation.validate_git_commit_sha("xyz1234"))
        self.assertFalse(validation.validate_git_commit_sha("g" * 40))

    def test_validate_json_structure_required_and_unexpected(self):
        ok, _ = validation.validate_json_structure({"a": 1, "b": 2}, ["a", "b"])
        self.assertTrue(ok)
        ok, err = validation.validate_json_structure({"a": 1}, ["a", "b"])
        self.assertFalse(ok)
        self.assertIn("b", err)
        # Unexpected fields when optional_fields given.
        ok, err = validation.validate_json_structure(
            {"a": 1, "z": 9}, ["a"], optional_fields=["b"]
        )
        self.assertFalse(ok)
        self.assertIn("z", err)

    def test_validate_path_safe_traversal(self):
        ok, err = validation.validate_path_safe("../etc")
        self.assertFalse(ok)
        self.assertIn("..", err)
        ok, _ = validation.validate_path_safe("normal/path")
        self.assertTrue(ok)


# --------------------------------------------------------------------------- #
# validation.check_state_file_age — stale lock detection
# --------------------------------------------------------------------------- #
class TestStateFileAge(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def _set_age(self, hours):
        self.f.write_text("{}")
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        os.utime(self.f, (old, old))

    def test_missing_is_fresh(self):
        ok, msg = validation.check_state_file_age(self.f, 24)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_recent_is_fresh(self):
        self._set_age(1)
        ok, _ = validation.check_state_file_age(self.f, 24)
        self.assertTrue(ok)

    def test_old_is_stale(self):
        self._set_age(25)
        ok, msg = validation.check_state_file_age(self.f, 24)
        self.assertFalse(ok)
        self.assertIn("hours old", msg)


if __name__ == "__main__":
    main()
