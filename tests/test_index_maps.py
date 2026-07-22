"""Tests for ``check-index-maps`` — the two-map drift gate.

Promotes the invariants that ``test_toc_completeness`` previously guarded via a
hand-maintained ``SPINE`` list into a data-driven check parsed from the two maps
themselves. Covers the three failure classes — vocabulary (bogus/missing status
tag), spine agreement (a read-map category with no creation-map home), and
seeded-really-created (a ``seeded`` row setup never writes) — plus the happy path.
Invokes the script end-to-end via subprocess (faithful L1), mirroring
``test_scaffold_strategy.py``.
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check-index-maps.py"
_INDEX = _REPO / "templates" / "project-index.md"
_TOC = _REPO / "templates" / "claude-md-toc.md"


def _run():
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)  # hermetic: force __file__-based root
    return subprocess.run([sys.executable, str(_SCRIPT)],
                          capture_output=True, text=True, env=env)


class HappyPathTests(TestCase):
    def test_clean_maps_pass(self):
        r = _run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK:", r.stdout)

    def test_emits_category_count(self):
        # The OK line surfaces how many read-map categories are covered — a
        # canary that the parser isn't silently under-counting rows.
        r = _run()
        self.assertRegex(r.stdout, r"\d+ read-map categories")


class _MapSandbox:
    """Swap one template file's content for the duration of a test, then restore."""

    def __init__(self, path: Path):
        self.path = path
        self._saved = None

    def __enter__(self):
        self._saved = self.path.read_text(encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.path.write_text(self._saved, encoding="utf-8")

    def replace(self, old, new):
        self.path.write_text(
            self.path.read_text(encoding="utf-8").replace(old, new),
            encoding="utf-8",
        )


class VocabularyDriftTests(TestCase):
    def test_bogus_status_tag_is_caught(self):
        with _MapSandbox(_INDEX) as m:
            m.replace("conductor/resource/glossary.md | on-demand",
                      "conductor/resource/glossary.md | manual")
            r = _run()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("missing a seeded/auto/on-demand status tag", r.stderr)
            self.assertIn("conductor/resource/glossary.md", r.stderr)


class SpineAgreementTests(TestCase):
    def test_read_map_category_absent_from_creation_map_is_caught(self):
        with _MapSandbox(_TOC) as m:
            # Drop the UX/UI row from the creation map; the read-map still lists
            # it → spine drift.
            m.replace("| UX/UI Spec              | `./conductor/requirement/ux-ui/design-spec.md`", "__removed__")
            r = _run()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("spine drift", r.stderr)
            self.assertIn("conductor/requirement/ux-ui", r.stderr)

    def test_creation_map_superset_is_not_drift(self):
        # The toc legitimately carries creation patterns the read-map groups
        # (decision-*.md, resource/<topic>.md, tracks/<track_id>). Adding a NEW
        # toc-only creation pattern must NOT trip the subset invariant.
        with _MapSandbox(_TOC) as m:
            m.replace("| **Management**  | Tracks Registry",
                      "| **Design**      | Some New Doc           | `./conductor/design/some-new-doc.md`                     | **on-demand** — create if missing.                                            |\n| **Management**  | Tracks Registry")
            r = _run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SeededReallyCreatedTests(TestCase):
    def test_seeded_row_not_written_by_setup_is_caught(self):
        # Tag git-flow.md (genuinely on-demand) as seeded — the precise lie the
        # user's bug was about (index promises a file setup never creates).
        with _MapSandbox(_INDEX) as m:
            m.replace("conductor/workflow/git-flow.md | on-demand",
                      "conductor/workflow/git-flow.md | seeded")
            r = _run()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("'seeded' but NOT written by setup", r.stderr)
            self.assertIn("conductor/workflow/git-flow.md", r.stderr)


class CategoryIndexLazyTests(TestCase):
    def test_seeded_category_index_is_caught(self):
        # A category index.md (lazy — created on first seed by corpus-writer)
        # must never be tagged 'seeded'. Re-tag the api-specs index row, which
        # is genuinely 'auto'.
        with _MapSandbox(_INDEX) as m:
            m.replace("conductor/design/api-specs/index.md | auto",
                      "conductor/design/api-specs/index.md | seeded")
            r = _run()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("category indices are lazy", r.stderr)
            self.assertIn("conductor/design/api-specs/index.md", r.stderr)


if __name__ == "__main__":
    main()
