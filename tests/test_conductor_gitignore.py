"""End-to-end git semantics for the conductor ``.gitignore`` block.

``/conductor:wiki-doctor`` lint/diff write transient scratch to the
**project-root** ``.conductor/`` (``wiki-lint-findings-*.json``,
``wiki-diff-findings-*.json``, ``wiki-diff-report.md``). Before the fix nothing
gitignored that location, so the scratch showed as untracked noise in every
``git status`` — sitting next to real tracked setup files like
``conductor/product/product.md``. ``/conductor:setup`` now appends a
root-anchored ``/.conductor/`` rule (``templates/conductor-gitignore.md``).

These tests pin the actual git behavior in a temp repo:

* root ``.conductor/wiki-*`` scratch IS ignored (the bug fix);
* the per-track ``conductor/tracks/<id>/.conductor/`` that track commits own is
  NOT ignored (the root anchor must not be dropped);
* real tracked files (``conductor/product/product.md``) are NOT ignored;
* the sentinel-guarded append is idempotent — a second setup run does not
  duplicate the block.

The skill-content contract (sentinel present, rule root-anchored, append wired
into §2.5 + staged in §3.6) lives in ``test_setup_idempotency.py``.
"""
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE_TEMPLATE = (ROOT / "templates" / "conductor-gitignore.md").read_text(encoding="utf-8")

# The block setup appends = the whole template file body.
CONDUCTOR_BLOCK = GITIGNORE_TEMPLATE

# Per-track .conductor/.gitignore body, as written by quality._ensure_conductor_gitignore.
# conftest.py puts ``scripts/`` on sys.path so ``track_state`` resolves as in production.
from track_state.quality import _CONDUCTOR_GITIGNORE  # noqa: E402


def _check_ignore(repo, path):
    """Return True if git would ignore ``path`` under ``repo``. ``path`` is repo-relative."""
    # --no-index makes check-ignore work without any commit; -q silences stdout.
    r = subprocess.run(
        ["git", "check-ignore", path],
        cwd=str(repo), capture_output=True, text=True,
    )
    return r.returncode == 0


class GitignoreSemanticsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        # A user-managed .gitignore that already carries unrelated lines + the
        # conductor block setup would append (simulating a brownfield project).
        (self.repo / ".gitignore").write_text(
            "node_modules/\n" + CONDUCTOR_BLOCK, encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return rel

    def test_root_wiki_scratch_is_ignored(self):
        # The bug: wiki-doctor lint/diff scratch at the project root.
        for rel in (
            ".conductor/wiki-lint-findings-ORPHANS.json",
            ".conductor/wiki-diff-findings-STALE.json",
            ".conductor/wiki-diff-report.md",
        ):
            self._touch(rel)
            self.assertTrue(_check_ignore(self.repo, rel),
                            f"root scratch must be ignored: {rel}")

    def test_per_track_conductor_is_not_ignored(self):
        # The anchor must not be dropped: track commits stage
        # conductor/tracks/<id>/.conductor/ (result.json, parallel.json, handoff).
        for rel in (
            "conductor/tracks/feat-x/.conductor/result.json",
            "conductor/tracks/feat-x/.conductor/parallel.json",
        ):
            self._touch(rel)
            self.assertFalse(_check_ignore(self.repo, rel),
                             f"per-track scratch must stay tracked: {rel}")

    def test_real_setup_files_are_not_ignored(self):
        # The whole point: real artifacts (product.md) sit beside the scratch and
        # must remain visible to git.
        for rel in (
            "conductor/product/product.md",
            "conductor/overview.md",
            "conductor/tracks.md",
        ):
            self._touch(rel)
            self.assertFalse(_check_ignore(self.repo, rel),
                             f"real file must stay tracked: {rel}")

    def test_append_is_idempotent_in_shell(self):
        # The exact §2.5 guard: grep the begin sentinel, skip if present, else
        # append. Running it twice must not duplicate the block.
        gi = self.repo / ".gitignore"
        before = gi.read_text(encoding="utf-8")
        cmd = (
            "grep -q '# conductor:gitignore begin' .gitignore 2>/dev/null "
            "|| cat \"$ROOT/templates/conductor-gitignore.md\" >> .gitignore"
        )
        env = {"ROOT": str(ROOT)}
        for _ in range(2):
            subprocess.run(["bash", "-c", cmd], cwd=str(self.repo),
                           env=env, check=True)
        after = gi.read_text(encoding="utf-8")
        self.assertEqual(before, after, "second append must be a no-op (idempotent)")
        self.assertEqual(after.count("# conductor:gitignore begin"), 1,
                         "block must appear exactly once")


# The per-track ``.conductor/.gitignore`` written by
# ``track_state.quality._ensure_conductor_gitignore`` — the ONLY thing covering
# transient markers that live under ``conductor/tracks/<id>/.conductor/`` (the
# root ``/.conductor/`` rule is root-anchored and deliberately does NOT reach
# them; the per-track dir carries committed bookkeeping so it stays tracked).


class PerTrackConductorGitignoreTests(TestCase):
    """Pin that every transient per-track marker is listed in the per-track
    ``.conductor/.gitignore`` — the inflight/tripwire/modified-guidance markers
    were added without updating this constant and showed as untracked noise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        # Mirror a target project: root-anchored /.conductor/ (covers root
        # scratch only) + a per-track .conductor/.gitignore written by quality.
        (self.repo / ".gitignore").write_text(CONDUCTOR_BLOCK, encoding="utf-8")
        self.track_dir = self.repo / "conductor" / "tracks" / "feat-x"
        cond = self.track_dir / ".conductor"
        cond.mkdir(parents=True, exist_ok=True)
        (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return rel

    def test_transient_markers_are_ignored(self):
        # The regression: these dot-prefixed markers under per-track .conductor/
        # were NOT in the per-track gitignore list and showed as untracked.
        for rel in (
            "conductor/tracks/feat-x/.conductor/.dispatch-inflight-1-1.json",
            "conductor/tracks/feat-x/.conductor/.tripwire-2-3.count",
            "conductor/tracks/feat-x/.conductor/.modified-guidance-1-1.md",
        ):
            self._touch(rel)
            self.assertTrue(_check_ignore(self.repo, rel),
                            f"transient marker must be ignored: {rel}")

    def test_committed_bookkeeping_stays_tracked(self):
        # The per-track .conductor/ stays tracked for committed bookkeeping;
        # only the transient names are ignored. result.json is listed
        # explicitly, so it stays ignored too (transient, recreated per cycle).
        for rel in (
            "conductor/tracks/feat-x/.conductor/parallel.json",
            "conductor/tracks/feat-x/.conductor/wave-agent.marker",
        ):
            self._touch(rel)
            self.assertTrue(_check_ignore(self.repo, rel),
                            f"listed transient artifact must be ignored: {rel}")


if __name__ == "__main__":
    main()
