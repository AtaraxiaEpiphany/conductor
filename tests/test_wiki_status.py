"""Tests for wiki_status: read-only wiki health metrics emitted by `wiki-status`."""
import io
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.wiki_status.status import cmd_status, _classify_freshness


def _out_captured(fn, *args, **kwargs):
    """Capture stdout JSON from a cmd call."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _build_tree(root, overview_age_days=0, with_log=True, broken_link=True):
    """Build a minimal conductor/ wiki tree under root."""
    cond = Path(root) / "conductor"
    (cond / "design" / "architecture").mkdir(parents=True)
    (cond / "tracks" / "foo").mkdir(parents=True)

    (cond / "overview.md").write_text(
        f"# Project Overview\n> Last updated: {_iso(overview_age_days)}\n"
    )
    if with_log:
        (cond / "log.md").write_text(
            "# Documentation Log\n\n## Entries\n\n"
            "| Timestamp | Track | Operation | Files | Summary |\n"
            "|-----------|-------|-----------|-------|---------|\n"
            f"| {_iso(10)} | auth | DOC_UPDATE | design/auth.md | Added auth flow |\n"
            f"| {_iso(1)} | wiki | QUERY_SAVE | queries/tech-stack.md | Query: tech stack |\n"
        )
    (cond / "index.md").write_text("# Index\n")
    (cond / "design" / "architecture" / "system-architecture.md").write_text(
        "# System Architecture\n"
        "See [[conductor/overview]]"
        + (" and [[broken-page]]." if broken_link else ".")
        + "\n"
    )
    (cond / "tracks.md").write_text(
        "# Tracks Registry\n- [x] done\n- [~] wip\n- [ ] new\n"
    )
    (cond / "tracks" / "foo" / "spec.md").write_text("# spec (track artifact)\n")


class WikiStatusTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _build_tree(self.tmp)

    def test_ok_payload(self):
        d = _out_captured(cmd_status, self.tmp)
        self.assertEqual(d["status"], "ok")
        # overview, log, index, system-architecture, tracks.md = 5
        # (tracks/foo/spec.md is a track artifact, excluded)
        self.assertEqual(d["document_count"], 5)
        self.assertEqual(d["log"]["entries"], 2)
        self.assertEqual(d["log"]["last_summary"], "Query: tech stack")
        self.assertEqual(d["overview"]["classification"], "fresh")
        self.assertEqual(d["orphan_scan"]["broken_count"], 1)
        self.assertEqual(d["orphan_scan"]["broken_targets"], ["broken-page"])
        self.assertEqual(d["tracks"]["completed"], 1)
        self.assertEqual(d["tracks"]["in_progress"], 1)
        self.assertEqual(d["tracks"]["new"], 1)

    def test_orphan_resolution_resolves_good_link(self):
        """[[conductor/overview]] must NOT be flagged — only the truly broken target."""
        d = _out_captured(cmd_status, self.tmp)
        self.assertIn("broken-page", d["orphan_scan"]["broken_targets"])
        self.assertNotIn("conductor/overview", d["orphan_scan"]["broken_targets"])

    def test_no_broken_links(self):
        tmp = tempfile.mkdtemp()
        _build_tree(tmp, broken_link=False)
        d = _out_captured(cmd_status, tmp)
        self.assertEqual(d["orphan_scan"]["broken_count"], 0)

    def test_infra_missing_when_overview_absent(self):
        Path(self.tmp, "conductor", "overview.md").unlink()
        d = _out_captured(cmd_status, self.tmp)
        self.assertEqual(d["status"], "infra_missing")
        self.assertEqual(d["missing"], ["conductor/overview.md"])

    def test_infra_missing_when_log_absent(self):
        Path(self.tmp, "conductor", "log.md").unlink()
        d = _out_captured(cmd_status, self.tmp)
        self.assertEqual(d["status"], "infra_missing")
        self.assertIn("conductor/log.md", d["missing"])

    def test_empty_log(self):
        Path(self.tmp, "conductor", "log.md").write_text(
            "# Documentation Log\n\n## Entries\n\n"
            "| Timestamp | Track | Operation | Files | Summary |\n"
            "|-----------|-------|-----------|-------|---------|\n"
        )
        d = _out_captured(cmd_status, self.tmp)
        self.assertEqual(d["log"]["entries"], 0)
        self.assertIsNone(d["log"]["last_timestamp"])


class FreshnessClassificationTests(TestCase):
    def test_fresh(self):
        self.assertEqual(_classify_freshness(_iso(3)), "fresh")

    def test_stale(self):
        self.assertEqual(_classify_freshness(_iso(20)), "stale")

    def test_outdated(self):
        self.assertEqual(_classify_freshness(_iso(60)), "outdated")

    def test_unparseable_is_outdated(self):
        self.assertEqual(_classify_freshness("not-a-date"), "outdated")

    def test_missing_timestamp_classifies_outdated(self):
        tmp = tempfile.mkdtemp()
        _build_tree(tmp)
        Path(tmp, "conductor", "overview.md").write_text("# no timestamp here\n")
        d = _out_captured(cmd_status, tmp)
        self.assertEqual(d["overview"]["classification"], "outdated")


if __name__ == "__main__":
    main()
