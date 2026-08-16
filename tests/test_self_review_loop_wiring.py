"""Structural tests for the opt-in self-review ("Ralph Wiggum") loop in the
implement skill (§3.6b).

The loop is DEFAULT OFF — it must not impose latency unless a task opts in.
These assert the opt-in gate + the bounded iteration + escalation contract are
wired into the implement dispatch flow, so the section can't be silently
removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import dispatch

ROOT = Path(__file__).resolve().parent.parent


class SelfReviewLoopWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")

    def test_section_present(self):
        self.assertIn("### 3.6b Self-Review Loop", self.skill)

    def test_success_path_routes_through_loop(self):
        # §3.6 SUCCESS must hand off to the opt-in follow-ups in order (not skip
        # straight to §3.7): self_review (§3.6b) → refactor (§3.6c) → §3.7, keyed
        # on the finalize envelope's opt-in keys.
        self.assertIn("`self_review` (§3.6b) → `refactor` (§3.6c)", self.skill)

    def test_default_off(self):
        self.assertIn("DEFAULT OFF", self.skill)

    def test_opt_in_signals(self):
        # Per-task marker + global env opt-in.
        self.assertIn("[Review]", self.skill)
        self.assertIn("CONDUCTOR_SELF_REVIEW", self.skill)

    def test_marker_is_not_an_exemption_tag(self):
        # [Review] must NOT enter the Docs/Config/Chore exemption logic — a
        # reviewable task still owes F2/F3.
        self.assertIn("name marker, not a tag", self.skill)

    def test_dispatches_code_reviewer(self):
        self.assertIn("conductor:code-reviewer", self.skill)
        self.assertIn("REVISION_RANGE", self.skill)

    def test_review_range_binds_code_sha(self):
        # §3.6b's code-reviewer REVISION_RANGE binds code_sha (the task's code
        # commit), not sha (the conductor chore commit). Binding sha made
        # [Review] always read "clean" — the chore diff is state files; code_sha
        # is the task's actual code, so review now sees real findings.
        # Rail A paste-verbatim (design D3): the binding lives in the CODE
        # builder (`_build_self_review_prompt`, emitted on the finalize
        # envelope's `self_review.prompt`); the skill pastes it verbatim.
        prompt = dispatch._build_self_review_prompt("/td", {"track_id": "x"}, "abc1234")
        self.assertIn("REVISION_RANGE=abc1234~1..abc1234", prompt)
        self.assertNotIn("~1..", prompt.replace("abc1234~1..abc1234", ""))
        self.assertIn("self_review.prompt", self.skill)

    def test_bounded_by_convergence_and_budget(self):
        # No runaway loop — convergence (loop-until-dry) capped by a 3-iteration
        # budget, deduping vs a `seen` set. Replaces the old "ONE fix iteration".
        self.assertIn("loop-until-dry", self.skill)
        self.assertIn("max 3 fix iterations", self.skill)
        self.assertIn("`seen`", self.skill)

    def test_escalates_only_on_residual_judgment(self):
        # Human (AskUserQuestion) is pulled in only for residual Critical, per
        # §3.6b "escalates to human only when judgment is required".
        self.assertIn("AskUserQuestion", self.skill)


class SeenSetPersistenceTests(TestCase):
    """The `seen`-signature set is loop state. A mid-loop context compaction would
    lose a model-resident set → redundant re-review of already-triaged findings
    (wasted fix iterations). §3.6b persists it to a conductor-owned, gitignored
    file keyed by task, so the loop survives compaction without a later `[Review]`
    task inheriting a stale set."""

    def setUp(self):
        self.skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")

    def test_seen_persists_to_conductor_owned_file(self):
        self.assertIn("review-seen.json", self.skill)
        self.assertIn(".conductor/review-seen.json", self.skill)

    def test_file_keyed_by_task_sha(self):
        # A new/different task must NOT inherit another task's seen set — the file
        # guards on task_sha so only a resume of the SAME task restores the set.
        self.assertIn("task_sha", self.skill)

    def test_cleared_on_terminal_exit(self):
        # The file is deleted on every terminal exit (dry/clean or budget-spent)
        # so a later [Review] task starts empty.
        lower = self.skill.lower()
        self.assertIn("delete", lower)


class ReviewSeenGitignoreTests(TestCase):
    """review-seen.json is transient loop state under .conductor/ — it must be in
    the conductor-gitignore tuple so it is never staged by a conductor commit
    (alongside the other transient markers). Asserted against the DERIVED
    _TRANSIENT_MARKERS (runtime truth), not the module source text — the
    filename is single-homed in lib.constants now."""

    def test_review_seen_in_conductor_gitignore(self):
        from scripts.track_state.quality import _TRANSIENT_MARKERS
        patterns = {pattern for pattern, _sample in _TRANSIENT_MARKERS}
        self.assertIn("review-seen.json", patterns)


if __name__ == "__main__":
    main()
