"""Registry-parser parity lint (campaign 2.6).

The tracks registry (``conductor/tracks.md``) is parsed by TWO independent
readers that must agree on coverage — a track one parser sees and the other
misses is invisible to half the surface (e.g. ``status`` renders it, the
write-guard doesn't):

1. ``track_state.misc._iter_registry_entries`` — the read-side state engine
   (checkbox / section / table formats, tolerant fallback),
2. ``lib.path_utils.extract_track_dirs`` — the hook-side extractor
   (same three formats + a dated-token backstop, project-root-relative).

Both docstrings already promise lockstep ("mirroring …" in both directions) —
this test is the enforcement. A shared corpus exercises every format and both
link orientations; the parsers must resolve the SAME set of track dirs.
"""
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase, main

from scripts.lib.path_utils import extract_track_dirs
from scripts.track_state.misc import _iter_registry_entries

CORPUS = textwrap.dedent("""\
    # Track Registry

    - [x] Auth flow (conductor/tracks/auth_20260101/)
    - [~] Data import — legacy (see old notes) (tracks/data_20260102/)
    - [ ] Payments, no dir yet (conductor/tracks/pay_20260103/)

    ### wiki_20260104

    - **Status:** in_progress
    - **Path:** [wiki](tracks/wiki_20260104/)

    | id | type | status | description |
    | --- | --- | --- | --- |
    | search_20260105 | feature | completed | search surface |

    Prose mentioning nothing parseable. Plain noise line.
    """)

# Entries the corpus MUST surface (pay's dir deliberately does not exist on
# disk — the stale-entry path; both parsers must still resolve a deterministic
# dir for it rather than dropping it).
EXPECTED_IDS = {
    "auth_20260101", "data_20260102", "pay_20260103",
    "wiki_20260104", "search_20260105",
}


def _build_project():
    """A temp project with the canonical conductor/tracks layout + corpus."""
    d = tempfile.TemporaryDirectory()
    proj = Path(d.name)
    conductor = proj / "conductor"
    (conductor / "tracks").mkdir(parents=True)
    for tid in EXPECTED_IDS - {"pay_20260103"}:
        (conductor / "tracks" / tid).mkdir()
    (conductor / "tracks.md").write_text(CORPUS, encoding="utf-8")
    return d, proj, conductor


class RegistryParserParityTests(TestCase):

    def setUp(self):
        self._d, self.proj, self.conductor = _build_project()
        self.addCleanup(self._d.cleanup)

    def _misc_dirs(self):
        entries = _iter_registry_entries(CORPUS, self.conductor)
        out = {}
        for e in entries:
            p = Path(e["track_dir"])
            if p.is_absolute():
                try:
                    p = p.relative_to(self.proj)
                except ValueError:
                    pass
            out[e["track_id"]] = p.as_posix()
        return out

    def _extract_dirs(self):
        return {Path(d).name: d for d in
                extract_track_dirs(self.conductor / "tracks.md")}

    def test_same_track_set(self):
        misc, extract = self._misc_dirs(), self._extract_dirs()
        self.assertEqual(set(misc), EXPECTED_IDS,
                         f"misc parser missed: {EXPECTED_IDS - set(misc)}; "
                         f"extra: {set(misc) - EXPECTED_IDS}")
        self.assertEqual(set(extract), EXPECTED_IDS,
                         f"extract parser missed: {EXPECTED_IDS - set(extract)}; "
                         f"extra: {set(extract) - EXPECTED_IDS}")

    def test_same_resolved_dirs(self):
        misc, extract = self._misc_dirs(), self._extract_dirs()
        for tid in sorted(EXPECTED_IDS):
            self.assertEqual(
                misc[tid], extract[tid],
                f"{tid}: misc resolved {misc[tid]!r} but extract resolved "
                f"{extract[tid]!r}")

    def test_all_formats_present_in_corpus(self):
        # Guard the corpus itself: if a format line stops matching ANY parser
        # (regex drift), the parity tests above would still pass on a corpus
        # that no longer exercises that format.
        misc = self._misc_dirs()
        by_marker = {tid: m for tid, m in
                     ((_e["track_id"], _e["marker"]) for _e in
                      _iter_registry_entries(CORPUS, self.conductor))}
        # checkbox rows carry their marker; section/table rows carry None.
        self.assertEqual(by_marker["auth_20260101"], "x")
        self.assertIsNone(by_marker["wiki_20260104"])
        self.assertIsNone(by_marker["search_20260105"])

    def test_stale_entry_surfaces_in_both(self):
        # pay_20260103's dir does not exist — the stale-entry path. Both
        # parsers must still resolve a deterministic canonical dir for it.
        misc, extract = self._misc_dirs(), self._extract_dirs()
        self.assertTrue(misc["pay_20260103"].endswith("pay_20260103"))
        self.assertEqual(extract["pay_20260103"], "conductor/tracks/pay_20260103")

    def test_missing_registry_file_is_empty(self):
        self.assertEqual(extract_track_dirs(self.proj / "nope.md"), [])


if __name__ == "__main__":
    main()
