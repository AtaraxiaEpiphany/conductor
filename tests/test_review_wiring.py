"""Structural tests for the adversarial, lensed review flow.

`conductor:review` fans the producer out **per lens** (one focused `code-reviewer`
pass per review dimension — `bugs`/`security`/`spec-compliance`/`tests`), runs a
completeness critic concurrently with the fan-out, then an adversarial refuter
as a per-lens barrier, and a bounded convergence loop over the fan-out until a
dry round — all over the shared `code-reviewer` analysis core. `code-reviewer`
gains optional MODE / FINDINGS_JSON / RESULT_PATH / LENS params whose defaults
preserve the single-pass behavior (so the post-loop auto-review and "Apply Fixes"
path stay green). The LENS §3.1 context-gate is load-bearing: without it, an
N-lens fan-out is an N× full-context cost regression. These assert the wiring so
the structure and the opt-in params can't be silently removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class CodeReviewerParamTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "code-reviewer.md").read_text(encoding="utf-8")

    def test_mode_param_documented(self):
        self.assertIn("`MODE`", self.agent)
        self.assertIn("`full`", self.agent)
        self.assertIn("`refute`", self.agent)
        self.assertIn("`critique`", self.agent)

    def test_findings_json_and_result_path_documented(self):
        self.assertIn("`FINDINGS_JSON`", self.agent)
        self.assertIn("`RESULT_PATH`", self.agent)

    def test_result_path_defaults_to_canonical_location(self):
        # The default MUST stay review-result.json — the post-loop auto-review
        # and the §3.0 "Apply Fixes" path both read that exact file.
        self.assertIn(
            "{TRACK_DIR}/.conductor/review-result.json", self.agent)

    def test_refute_defaults_to_refuted_when_uncertain(self):
        # The cure for self-preferential bias: a finding that cannot be
        # positively re-confirmed does not survive.
        self.assertIn("refuted", self.agent.lower())

    def test_critique_reports_only_new_classes(self):
        # The completeness-critic surfaces what the producer missed, not
        # duplicates of what it already caught.
        self.assertIn("missed", self.agent.lower())

    def test_lens_param_documented(self):
        # A2: the LENS param narrows a pass to one review dimension, enabling the
        # per-lens producer fan-out.
        self.assertIn("`LENS`", self.agent)
        for lens in ("bugs", "security", "spec-compliance", "tests"):
            self.assertIn(lens, self.agent)

    def test_context_gate_short_circuits_non_relevant_sources(self):
        # The §3.1 gate is the load-bearing cost control: a lensed pass loads
        # only its lens-relevant global sources, so a 4-lens fan-out does not
        # cost 4x the full-context budget. Without it, the fan-out is a cost
        # regression. Assert the gate exists AND that §3.1 honors it (no longer
        # loads unconditionally under a lens) AND explicitly skips the rest.
        self.assertIn("§2.6", self.agent)
        self.assertIn("gates §3.1", self.agent.lower())
        self.assertIn("gated by", self.agent.lower())
        self.assertIn("skip any §3.1 source not in", self.agent.lower())

    def test_lens_json_field_emitted(self):
        # The §4.1 JSON carries a "lens" field so synthesis can group per-lens
        # result files.
        self.assertIn('"lens"', self.agent)


class ReviewLensedWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")

    def test_section_present(self):
        self.assertIn("### 2.3 Dispatch Code Review", self.skill)
        self.assertIn("producer", self.skill.lower())
        self.assertIn("refuter", self.skill.lower())
        self.assertIn("critic", self.skill.lower())

    def test_refute_pass_gated_on_critical_high(self):
        # The refute pass runs only when a lens found Critical/High — Medium/Low
        # aren't worth the latency.
        self.assertIn("Critical", self.skill)
        self.assertIn("MODE=refute", self.skill)
        self.assertIn("FINDINGS_JSON", self.skill)

    def test_critique_pass_uses_mode_param(self):
        self.assertIn("MODE=critique", self.skill)
        self.assertIn("RESULT_PATH", self.skill)
        self.assertIn("review-critique.json", self.skill)

    def test_lensed_producer_fan_out(self):
        # A2: the producer is fanned out per lens (4 focused passes), not one
        # holistic pass. Each lens gets its own LENS + per-lens RESULT_PATH.
        self.assertIn("lens producer fan-out", self.skill.lower())
        self.assertIn("LENS=", self.skill)
        for lens in ("bugs", "security", "spec-compliance", "tests"):
            self.assertIn(lens, self.skill)
        self.assertIn("review-lens-{lens}.json", self.skill)

    def test_lensed_fanout_and_critic_dispatched_concurrently(self):
        # The 4 lens producers AND the critic are dispatched in ONE message —
        # they neither read nor block on each other. The refuter is the one true
        # barrier (it consumes each lens's findings after that lens lands).
        self.assertIn("ONE message", self.skill)
        self.assertIn("concurrent", self.skill.lower())
        self.assertIn("5 dispatches", self.skill)

    def test_refuter_runs_per_lens(self):
        # The refute re-confirms findings within their own lens dimension, writing
        # a per-lens survivor file.
        self.assertIn("LENS={lens}", self.skill)
        self.assertIn("review-lens-{lens}-refute.json", self.skill)

    def test_convergence_loop_is_bounded_and_dry_stopped(self):
        # Loop-until-dry over the lens fan-out: re-run against an accumulating
        # `seen` set, stop on a dry round, hard cap so it can't burn latency.
        self.assertIn("seen", self.skill.lower())
        self.assertIn("dry", self.skill.lower())
        self.assertIn("2 lens-fan-out rounds", self.skill.lower())

    def test_synthesis_merges_lens_survivors_and_critic(self):
        # Merged findings = (union over per-lens refute survivors) ∪ critic's
        # newly-discovered classes.
        self.assertIn("B_{lens}", self.skill)
        self.assertIn("∪ C", self.skill)

    def test_apply_fixes_path_unchanged(self):
        # §3.0 still reads the canonical review-result.json (the merged file).
        self.assertIn("Apply Fixes", self.skill)
        self.assertIn("review-result.json", self.skill)


class ReviewVerdictPersistenceTests(TestCase):
    """The review VERDICT (APPROVE / CHANGES_REQUESTED / …) was ephemeral stdout —
    only the agent-RUN ``status`` reached disk, so a completed track's review
    judgment was nowhere auditable. These pin the persistence contract: the schema
    distinguishes verdict from run-status, code-reviewer writes verdict, and the
    post-loop teleoperator transcribes verdict + counts to the (committed, audited)
    sidecar. Non-blocking — the gate still advances on any non-FAILURE verdict."""

    def setUp(self):
        self.schema = (ROOT / "runtime" / "contracts" / "review-result-schema.md").read_text(encoding="utf-8")
        self.agent = (ROOT / "agents" / "code-reviewer.md").read_text(encoding="utf-8")
        self.post_loop_step = (ROOT / "skills" / "post-loop-step" / "SKILL.md").read_text(encoding="utf-8")

    def test_schema_distinguishes_verdict_from_run_status(self):
        # `status` is the agent-RUN status; `verdict` is the review judgment.
        # Both must appear and the doc must call out they are not the same thing.
        self.assertIn('"verdict"', self.schema)
        self.assertIn("APPROVE_WITH_COMMENTS", self.schema)
        lower = self.schema.lower()
        self.assertIn("agent-run", lower)  # status = agent-run; verdict = judgment
        self.assertTrue("do not confuse" in lower or "distinct" in lower)

    def test_code_reviewer_writes_verdict(self):
        # The agent must carry `verdict` in the JSON it writes (mirroring stdout).
        self.assertIn('"verdict"', self.agent)

    def test_post_loop_teleoperator_transcribes_counts(self):
        # The dispatch_review row transcribes STATUS + CRITICAL/HIGH counts so the
        # spine can stamp verdict + counts to the sidecar for audit.
        row = self.post_loop_step.split("`dispatch_review`", 1)[1].split("\n|", 1)[0]
        self.assertIn("--status", row)
        self.assertIn("--critical", row)
        self.assertIn("--high", row)


class SpecReviewerFourQuadrantLensTests(TestCase):
    """spec-reviewer §3.3 holds the four-quadrant stance as a read-only **lens**
    (no turns, no questions — findings only) over `spec.md`. EARS checks *form*;
    the lens checks *substance*: a propagated wrong premise (Q3) and an
    operationalizable unknown confessed as "TBD" (Q4). The canonical stance lives
    in the grill-discipline contract; this section is a thin consumer that cites it
    (Read-on-demand pointer), so the stance has one home and a future drift lint
    passes. The lens stays read-only — the `tools:` line is still pinned to
    Read/Grep/Glob by ``test_on_subagent_stop_recovery.py``."""

    def setUp(self):
        self.agent = (ROOT / "agents" / "spec-reviewer.md").read_text(encoding="utf-8")

    def test_section_heading_present(self):
        # New §3.3 sits between EARS conformance (§3.2) and the Plan Audit (§3.4).
        self.assertIn("### 3.3 Spec Audit (four-quadrant lens)", self.agent)
        # Renumber cascade held: the old §3.3 is now §3.4.
        self.assertIn("### 3.4 Plan Audit", self.agent)

    def test_lens_cites_grill_contract(self):
        # The stance is single-homed in the contract; the lens must cite it
        # (Read-on-demand pointer), not restate the four labels here.
        self.assertIn("runtime/contracts/grill-discipline", self.agent)

    def test_lens_flags_q3_and_q4(self):
        # Q3 = propagated wrong premise (Out of Scope copied verbatim); Q4 =
        # operationalizable unknown (decidable by experiment). Both surface as
        # pipe-delimited findings (any finding → CHANGES_REQUESTED, per §3.7).
        self.assertIn("Q3", self.agent)
        self.assertIn("Q4", self.agent)


if __name__ == "__main__":
    main()
