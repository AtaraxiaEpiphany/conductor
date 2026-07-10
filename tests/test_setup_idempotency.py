"""Phase 4 hardening: /conductor:setup must be safe to re-run mid-flight.

Three footguns existed on the setup path, all surfaced by resuming an interrupted
setup (which re-enters at the first unsaved resume key):

1. **§2.5 CLAUDE.md TOC append was not idempotent.** Re-entering §2.5 (key
   ``2.5_finalization`` not yet saved) re-appended the whole ``# Conductor`` File
   Index block, duplicating it. Fix: the template now carries
   ``<!-- conductor:toc begin -->`` … ``<!-- conductor:toc end -->`` sentinels
   and the skill skips the append when the begin sentinel is already present.

2. **§3.6 staged with ``git add -A``.** A brownfield project can carry unrelated
   WIP; ``-A`` sweeps it into the ``chore(conductor): Scaffold conductor setup``
   commit. Fix: scope the stage to what setup owns — ``conductor/`` + ``CLAUDE.md``.

3. **§2.0 re-dispatched ``project-analyzer`` on resume.** §2.0 saves NO resume
   key (the chain starts at ``2.1_product_guide``), so an interruption after the
   analyzer ran → re-run sees no marker → re-dispatches the expensive analysis and
   overwrites ``analysis.json``. Fix: ``analysis.json``'s existence is the
   checkpoint — if present, Read it to recover the detection fields and skip the
   dispatch.

These pin the three fixes so a regression (sentinel dropped, ``-A`` reintroduced,
guard removed) is caught here, not during a real interrupted-setup resume.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
TOC = (ROOT / "templates" / "claude-md-toc.md").read_text(encoding="utf-8")
GITIGNORE = (ROOT / "templates" / "conductor-gitignore.md").read_text(encoding="utf-8")

BEGIN_SENTINEL = "<!-- conductor:toc begin -->"
END_SENTINEL = "<!-- conductor:toc end -->"


class ClaudeMdTocIdempotencyTests(TestCase):
    def test_template_carries_begin_sentinel(self):
        self.assertIn(BEGIN_SENTINEL, TOC,
                      "TOC template must bracket its block with the begin sentinel")

    def test_template_carries_end_sentinel(self):
        self.assertIn(END_SENTINEL, TOC,
                      "TOC template must bracket its block with the end sentinel")

    def test_begin_sentinel_precedes_content(self):
        # The sentinel must come BEFORE the visible block, else the skip-check
        # would fire only after a partial duplicate already landed.
        self.assertLess(TOC.index(BEGIN_SENTINEL), TOC.index("# Conductor"))

    def test_end_sentinel_follows_content(self):
        self.assertGreater(TOC.index(END_SENTINEL), TOC.index("# Conductor"))

    def test_skill_skips_append_when_sentinel_present(self):
        # The idempotency gate itself: §2.5 must check for the begin sentinel and
        # skip the append when it is already in the project's CLAUDE.md.
        self.assertIn(BEGIN_SENTINEL, SKILL)
        self.assertIn("skip the append", SKILL)

    def test_skill_documents_both_sentinels(self):
        # Drift guard: the skill must reference the SAME begin sentinel the
        # template writes (so the skip-check matches what the append produces)
        # AND name the end sentinel, so the bracketing contract is visible at
        # the append site, not just embedded in the template.
        self.assertIn(BEGIN_SENTINEL, SKILL)
        self.assertIn(END_SENTINEL, SKILL)


def _bash_fence():
    """The §3.6 fenced bash command (the scoped scaffold commit). Targeted to the
    commit fence: setup §2.3/§2.4/§2.5 now carry their own cp/sed bash fences, so
    the first ``bash`` fence in the skill is no longer the commit one. Enumerate
    every fenced block and pick the one whose body runs ``git commit``."""
    import re
    blocks = re.findall(r"[ \t]*```bash\n(.*?)\n[ \t]*```", SKILL, re.S)
    commit_blocks = [b for b in blocks if "git commit" in b]
    assert commit_blocks, "§3.6 must keep its scoped-commit bash command fence"
    return commit_blocks[0]


class ScopedGitAddTests(TestCase):
    def test_final_commit_does_not_use_git_add_all(self):
        # ``git add -A`` sweeps unrelated brownfield WIP into the scaffold commit.
        # The fenced command must scope its stage, not use the bare ``-A`` sweep.
        # (The §3.6 prose may still *mention* ``-A`` to forbid it — only the
        # fenced command is authoritative here.)
        self.assertNotIn("git add -A", _bash_fence(),
                         "scaffold commit must scope its stage, not ``git add -A``")

    def test_final_commit_scopes_to_conductor_tree(self):
        self.assertIn("git add conductor/", _bash_fence())

    def test_final_commit_includes_claude_md(self):
        # The CLAUDE.md TOC append lives at project root (outside conductor/), so
        # the scoped stage must name it explicitly or the append is lost.
        self.assertIn("CLAUDE.md", _bash_fence())

    def test_diff_cached_guard_preserved(self):
        # The defensive re-run no-op guard must survive the rescoping.
        self.assertIn("git diff --cached --quiet", SKILL)

    def test_final_commit_stages_gitignore(self):
        # §2.5 now appends a conductor block to the project-root .gitignore; the
        # scoped scaffold commit must stage it or the append is left uncommitted.
        self.assertIn(".gitignore", _bash_fence())


class ConductorGitignoreTests(TestCase):
    """§2.5 step 2 scaffolds a sentinel-guarded .gitignore block so wiki-doctor's
    project-root .conductor/ scratch (wiki-lint-findings-*.json, wiki-diff-*.json,
    wiki-diff-report.md) stops showing as untracked noise in every git status.
    The rule must be ROOT-anchored so the committed per-track
    conductor/tracks/*/.conductor/ is unaffected. (End-to-end git semantics are
    pinned in test_conductor_gitignore.py.)"""

    GI_BEGIN = "# conductor:gitignore begin"
    GI_END = "# conductor:gitignore end"

    def test_template_exists_and_carries_sentinels(self):
        self.assertIn(self.GI_BEGIN, GITIGNORE)
        self.assertIn(self.GI_END, GITIGNORE)

    def test_template_rule_is_root_anchored(self):
        # Drift guard: the rule must be ``/.conductor/`` (leading slash), NOT
        # ``.conductor/`` — the un-anchored form would also ignore the per-track
        # conductor/tracks/*/.conductor/ that track commits own.
        self.assertIn("/.conductor/\n", GITIGNORE)
        for line in GITIGNORE.splitlines():
            stripped = line.strip()
            if stripped.endswith(".conductor/"):
                self.assertTrue(stripped.startswith("/"),
                                f"gitignore rule must be root-anchored: {stripped!r}")

    def test_template_uses_hash_comment_sentinels(self):
        # .gitignore has no HTML-comment syntax — a ``<!-- -->`` sentinel would be
        # parsed as a (junk) ignore pattern. Sentinels must be ``#`` comments.
        self.assertNotIn("<!--", GITIGNORE)

    def test_skill_has_sentinel_guarded_append(self):
        # The idempotent append: grep the begin sentinel, skip if present, else
        # cat the template into the project-root .gitignore (>> creates if absent).
        self.assertIn(self.GI_BEGIN, SKILL)
        self.assertIn("templates/conductor-gitignore.md", SKILL)
        self.assertIn(">> .gitignore", SKILL)

    def test_skill_append_is_idempotent(self):
        # A setup re-run must never duplicate the block — the grep sentinel gate
        # must skip the append when the begin sentinel is already present.
        self.assertIn("skip the append", SKILL)

    def test_skill_states_root_anchored_rationale(self):
        # The skill prose must explain WHY the rule is root-anchored, so a future
        # edit does not drop the anchor and silently ignore per-track scratch.
        self.assertIn("root-anchored", SKILL)


class AnalysisJsonResumabilityTests(TestCase):
    def test_guard_skips_dispatch_when_analysis_exists(self):
        # §2.0 must gate the project-analyzer dispatch on analysis.json's absence.
        self.assertIn("conductor/.conductor/analysis.json", SKILL)
        self.assertIn("skip the dispatch", SKILL)

    def test_guard_reads_existing_file_to_recover_fields(self):
        # Skipping the dispatch is not enough — downstream steps (§2.3 pre-fill,
        # §3.2 description) need the detection fields back in context, so the
        # guard must Read the existing analysis.json.
        self.assertIn("Read it to recover", SKILL)

    def test_dispatch_remains_the_first_run_path(self):
        # The analyzer is still dispatched on a genuine first run (no analysis.json).
        self.assertIn("conductor:project-analyzer", SKILL)
        self.assertIn("PROJECT_DIR={project root}", SKILL)


if __name__ == "__main__":
    main()
