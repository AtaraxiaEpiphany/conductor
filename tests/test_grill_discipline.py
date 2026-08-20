"""Content + drift guards for the grill-discipline contract.

`runtime/contracts/grill-discipline.md` is the single home for the grill discipline
(four-quadrant stance, grill loop, premise-challenge pass, operationalize-unknowns,
signal-done) PLUS the net-new posture spectrum that decides when to grill at all.
Surfaces that grill (brief, and future adopters) Read this contract on demand and
follow it rather than restating it — a second restated home silently drifts
([[runtime/contracts/prose-style]] Bucket B).

These grep-style assertions pin the load-bearing prose so a deletion or silent edit
is caught, the same way ``test_brief_wiring.py`` pins brief's mechanics.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text()


CONTRACT = "runtime/contracts/grill-discipline.md"


class GrillDisciplineContractTests(TestCase):
    """The canonical grill procedure — single-homed in the contract."""

    def test_contract_has_frontmatter(self):
        txt = _read(CONTRACT)
        self.assertIn("type: concept", txt)
        self.assertIn("sources:", txt)
        self.assertIn("skills/brief", txt)  # first adopting surface
        self.assertIn("last_verified:", txt)

    def test_posture_spectrum_present(self):
        # The net-new gatekeeper: choose full-grill vs batch-confirm vs ask-nothing
        # vs four-quadrant-lens BEFORE grilling. Prevents over-application (grilling
        # a config doc or an executor wastes attention every turn).
        txt = _read(CONTRACT)
        self.assertIn("posture spectrum", txt.lower())
        for posture in ("Full grill", "Batch-confirm", "Ask-nothing",
                        "Four-quadrant as a lens"):
            self.assertIn(posture, txt, f"posture spectrum missing: {posture}")

    def test_four_quadrant_stance_labels_present(self):
        # The 2x2 (you x user x known x unknown) — canonical definitions live here,
        # not in any consumer (brief's restated copy was removed).
        txt = _read(CONTRACT)
        self.assertIn("SHARED-KNOWN", txt)
        self.assertIn("YOUR-KNOWN / USER-UNKNOWN", txt)
        self.assertIn("YOUR-UNKNOWN / USER-KNOWN", txt)
        self.assertIn("SHARED-UNKNOWN", txt)

    def test_grill_loop_rules_present(self):
        # AskUserQuestion-only questioning; look-it-up-first;
        # recommended-answer-first with rationale (not an interrogation).
        txt = _read(CONTRACT)
        self.assertIn("one question at a time", txt.lower())
        self.assertIn("AskUserQuestion", txt)
        self.assertIn("Look it up before you ask", txt)
        self.assertIn("(Recommended)", txt)

    def test_frontier_rounds_rule_present(self):
        # D1: the loop batches the frontier — the mutually-independent,
        # currently-unblocked decisions — at most 4 per AskUserQuestion call
        # (the tool cap). Dependent decisions stay one question at a time.
        txt = _read(CONTRACT)
        self.assertIn("frontier", txt.lower())
        self.assertIn("mutually-independent", txt.lower())
        self.assertIn("at most 4 questions", txt)

    def test_fact_dispatch_form_present(self):
        # D3: a non-trivial lookup dispatches a read-only subagent (the
        # explorer/doc-probe pattern) that runs while the round waits on the
        # human — facts never block decisions, full doc content never enters
        # the grill's context. Inline Read/Grep stays fine for one-liners.
        txt = _read(CONTRACT)
        self.assertIn("read-only subagent", txt)
        self.assertIn("explorer", txt)
        self.assertIn("doc-probe", txt)
        self.assertIn("one-liners", txt)

    def test_premise_challenge_pass_present_and_bounded(self):
        # Q3: pose at most ONE challenge before the convergent grill.
        txt = _read(CONTRACT)
        self.assertIn("Premise-challenge pass", txt)
        self.assertIn("questionable", txt)            # the trigger condition
        self.assertIn("at most one", txt.lower())     # bounded to one
        self.assertIn("verbatim", txt)                # Out-of-Scope propagation

    def test_operationalize_unknowns_present(self):
        # Q4: a shared-unknown decidable by experiment -> testable hypothesis.
        txt = _read(CONTRACT)
        self.assertIn("Operationalize", txt)
        self.assertIn("hypothesis", txt.lower())
        self.assertIn("single variable", txt.lower())
        self.assertIn("success/fail signal", txt.lower())

    def test_signal_done_rule_present(self):
        # The done-signal is the real gate, not the raw question count (a proxy
        # that's wrong exactly when the grill is done well).
        txt = _read(CONTRACT)
        self.assertIn("Signal grill-done", txt)
        self.assertIn("proxy", txt.lower())

    def test_shared_known_includes_settled_vocabulary_and_decisions(self):
        # D2: the glossary and decision records are SHARED-KNOWN readable
        # inputs — settled vocabulary and prior decisions are never re-asked.
        txt = _read(CONTRACT)
        self.assertIn("settled vocabulary", txt)
        self.assertIn("conductor/resource/glossary.md", txt)
        self.assertIn("conductor/design/decision-*.md", txt)

    def test_crystallization_writes_section_present(self):
        # D2 §7: the grill writes back — glossary entries on term
        # crystallization, decision records gated by the sparsity triple.
        txt = _read(CONTRACT)
        self.assertIn("Crystallization writes", txt)
        self.assertIn("Avoid-list", txt)          # rejected synonyms recorded
        self.assertIn("create the file if missing", txt)  # glossary create-if-missing
        self.assertIn("hard to reverse", txt)     # sparsity triple — all three
        self.assertIn("surprising without context", txt)
        self.assertIn("a real trade-off", txt)
        self.assertIn("never delete", txt)        # append-only records
        self.assertIn("small globals", txt)       # read directly, no dispatch

    def test_see_also_links_back_to_prose_style(self):
        # Bidirectionality (doc-conventions): prose-style links here, so this links
        # back, and core-contract is the resident sibling.
        txt = _read(CONTRACT)
        self.assertIn("[[runtime/contracts/prose-style]]", txt)
        self.assertIn("[[runtime/core-contract]]", txt)


if __name__ == "__main__":
    main()
