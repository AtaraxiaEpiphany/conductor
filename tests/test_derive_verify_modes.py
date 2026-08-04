"""Tests for ``verify_mode_profiles.derive_verify_modes`` — advisory phase-goal → modes.

The verify-mode analog of ``derive_task_tag``. Given a phase's goal as free
text, propose the modes a ``<!-- verify: <modes> -->`` directive should carry.
Empty list = emit no directive = the default full gate (correct for feature work).

Load-bearing invariants:

- **Advisory + fail-open**: ``init-from-plan --check`` still only *warns* on the
  final directive, and phase-checker no-ops ``anchor`` on an unfrozen track — so
  a wrong proposal self-corrects at the gate, never fatal. Any exception → ``[]``.
- **Precedence is explicit**: boot (final integration) > refactor+anchor >
  compile (migration intermediate) > default. The decision logic is encoded as
  ordered rules, not registry tokenization (which would rank ``test`` highest
  for almost everything).
- **Substring traps**: ``boot`` must not false-fire inside ``spring-boot``
  (a migration goal mentioning the Spring Boot framework is a *compile* goal).
- **Safe-default bias**: ``[]`` is the correct outcome for feature work and the
  migration's FINAL "tests pass" phase.
"""
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import verify_mode_profiles as vmp  # noqa: E402


class DeriveVerifyModesBasics(TestCase):
    """Canonical phase goals map to the expected mode directive, or []."""

    def test_final_integration_boots(self):
        self.assertEqual(vmp.derive_verify_modes("Wire up and boot the app"), ["test", "start"])

    def test_final_integration_starts(self):
        self.assertEqual(
            vmp.derive_verify_modes("Final integration phase — the app starts and tests are green"),
            ["test", "start"],
        )

    def test_migration_intermediate_compiles(self):
        self.assertEqual(vmp.derive_verify_modes("Migrate dependencies"), ["compile"])
        self.assertEqual(
            vmp.derive_verify_modes("Migrate source — javax to jakarta rename"), ["compile"],
        )
        self.assertEqual(
            vmp.derive_verify_modes("Phase 2: compile the source after the rename"), ["compile"],
        )

    def test_refactor_with_frozen_anchor(self):
        self.assertEqual(
            vmp.derive_verify_modes("Refactor internals — frozen anchor must hold"), ["anchor"],
        )
        self.assertEqual(
            vmp.derive_verify_modes(
                "Consolidate the modules; the pinned subset must not regress"),
            ["anchor"],
        )

    def test_feature_work_is_empty(self):
        self.assertEqual(vmp.derive_verify_modes("Add the payments feature"), [])
        self.assertEqual(vmp.derive_verify_modes("Standard feature work"), [])


class DeriveVerifyModesGuards(TestCase):
    """The decision-logic edges that distinguish a real directive phase from
    plain default-gate work."""

    def test_spring_boot_is_compile_not_start(self):
        """``boot`` inside ``spring-boot`` must NOT trigger the boot-smoke branch
        — this migration goal is a compile (intermediate) phase, not a boot phase.
        The classic substring trap the negative-lookbehind guards against."""
        self.assertEqual(
            vmp.derive_verify_modes("Bump spring-boot and make it build"), ["compile"],
        )

    def test_spaced_spring_boot_is_compile_not_start(self):
        """The spaced form ``Spring Boot`` (as a goal writes it, e.g. "Migrate to
        Spring Boot 3") must resolve to ``compile``, NOT ``test,start``. The
        hyphenated form is blocked by the ``[-\\w]`` lookbehind, but the spaced
        form leaves ``boot`` preceded by a bare space — which alone passes that
        lookbehind — so a second ``(?<!spring )`` lookbehind closes the gap. This
        is the original bug: the resolver routed a framework-migration goal to the
        boot-smoke branch."""
        self.assertEqual(
            vmp.derive_verify_modes("Migrate to Spring Boot 3"), ["compile"],
        )
        self.assertEqual(
            vmp.derive_verify_modes("Upgrade to spring boot 3.2"), ["compile"],
        )

    def test_genuine_app_boot_still_resolves_to_test_start(self):
        """The spaced-``spring boot`` fix must NOT over-fire and swallow a genuine
        app-boot goal. "Migrate source and boot" has ``boot`` preceded by a space
        but NOT by "spring ", so the boot-smoke branch still fires → test,start."""
        self.assertEqual(
            vmp.derive_verify_modes("Migrate source and boot"), ["test", "start"],
        )

    def test_intermediate_migration_incidental_green_is_compile(self):
        """An intermediate migration goal that only INCIDENTALLY mentions green
        ("keep the suite green", "stay green") still resolves to ``compile`` — the
        suite is the safety net there, not the gate. Bare ``green``/``suite green``
        are the incidental words that falsely routed such phases to the suite gate
        before; only an explicit pass-promise ("make tests pass") defeats compile."""
        self.assertEqual(
            vmp.derive_verify_modes(
                "Migrate the persistence layer, keeping the suite green"), ["compile"],
        )
        self.assertEqual(
            vmp.derive_verify_modes("Migrate the DAO layer and stay green"), ["compile"],
        )

    def test_plain_refactor_without_anchor_is_default(self):
        """A readability refactor with no frozen-anchor signal is just default
        TDD work — not an anchor phase. anchor requires BOTH refactor intent
        AND an anchor signal."""
        self.assertEqual(vmp.derive_verify_modes("Refactor for readability"), [])

    def test_migration_final_tests_pass_is_default(self):
        """The migration's FINAL phase — "tests pass" — wants the full gate, not
        compile. compile is only for intermediate phases where the suite is red;
        once the goal is a green suite, the default gate is correct."""
        self.assertEqual(vmp.derive_verify_modes("Migrate the suite until tests pass"), [])


class DeriveVerifyModesDebtCarrying(TestCase):
    """The debt-carrying migration phase — a phase that *deliberately* won't
    compile or pass until a LATER phase finishes the migration.

    This is the canonical Spring Boot parent-bump shape: P1 bumps the parent
    (consumers not yet fixed → won't compile), P2 fixes the consumers (compiles),
    P3 boots. P1 is debt-carrying → ``verify: none``. The keyword sets must reach
    ``["none"]`` here WITHOUT breaking the existing regression guards:

    - a bare ``"Migrate dependencies"`` goal still resolves to ``compile`` (the
      safe intermediate default) — debt-carry keywords require an explicit
      dep/version-mutation verb+object, not the bare migration noun.
    - ``"Bump … build"`` still resolves to ``compile`` (the NOT-compile guard).
    - ``"Bump … tests pass"`` still resolves to ``[]`` (the NOT-suite-green guard).
    """

    def test_bump_spring_boot_parent_is_none(self):
        """The load-bearing case from the bug report: bumping the Spring Boot
        parent without naming a build/test outcome is a pure debt-carry phase."""
        self.assertEqual(
            vmp.derive_verify_modes("Bump the spring-boot parent"), ["none"],
        )

    def test_update_dependencies_is_none(self):
        self.assertEqual(vmp.derive_verify_modes("Update dependencies"), ["none"])

    def test_major_version_bump_is_none(self):
        self.assertEqual(
            vmp.derive_verify_modes("Major version bump of the ORM"), ["none"],
        )

    def test_upgrade_dependencies_is_none(self):
        self.assertEqual(
            vmp.derive_verify_modes("Upgrade the dependencies to v2"), ["none"],
        )

    def test_dependency_bump_phrase_is_none(self):
        self.assertEqual(
            vmp.derive_verify_modes("dependency bump for the runtime"), ["none"],
        )

    def test_bump_with_build_stays_compile(self):
        """NOT-compile guard is load-bearing: 'Bump … build' contains both a
        debt-carry keyword and a compile keyword → must stay ``compile``, not
        ``none``. Preserves the regression guard semantics (see Guards class)."""
        self.assertEqual(
            vmp.derive_verify_modes("Bump spring-boot and make it build"),
            ["compile"],
        )

    def test_bump_with_tests_pass_stays_default(self):
        """NOT-suite-green guard: 'Bump … tests pass' carries a debt-carry
        keyword but the goal says the suite is green → the full gate, not none."""
        self.assertEqual(
            vmp.derive_verify_modes("Bump deps and make tests pass"), [],
        )

    def test_bare_migrate_dependencies_stays_compile(self):
        """The disambiguation case: a bare ``"Migrate dependencies"`` goal is
        AMBIGUOUS — it could be a debt-carrying bump or an intermediate that
        compiles. The debt-carry keywords require an explicit verb+object
        ('bump the …', 'update dependencies', 'major version'), so the bare
        migration noun falls through to the migration-intermediate ``compile``
        default. The author resolves the ambiguity by writing a specific goal."""
        self.assertEqual(vmp.derive_verify_modes("Migrate dependencies"), ["compile"])

    def test_debt_carry_then_boot_final(self):
        """The full canonical staged-migration shape, end to end: the deps-bump
        phase reaches ``none``, the source phase reaches ``compile``, and the
        final integration reaches ``test,start``."""
        self.assertEqual(
            vmp.derive_verify_modes("Bump the spring-boot parent"), ["none"],
        )
        self.assertEqual(
            vmp.derive_verify_modes("Migrate source — javax to jakarta rename"),
            ["compile"],
        )
        self.assertEqual(
            vmp.derive_verify_modes("Migrate source, wire up, and boot the app"),
            ["test", "start"],
        )


class DeriveVerifyModesVocabFilter(TestCase):
    """Every proposed mode is validated against the resolved ``MODE_VOCAB``.

    This closes the asymmetry the docstring advertised but the code didn't
    enforce: ``default_verify_for`` (the tag-derived path) filtered its proposals
    against ``MODE_VOCAB``, while ``derive_verify_modes`` returned bare literals.
    An overlay that *removed* a mode (dropped ``none`` or ``anchor``) would have
    let the resolver propose a mode the parser then warns on. Each list-returning
    branch now routes through ``_filter_to_vocab``.
    """

    def test_shipped_vocab_proposals_pass_through(self):
        """Against the shipped vocab, every branch's proposal is unchanged — the
        filter is pure additive safety with no behavior change for the baseline."""
        self.assertEqual(vmp.derive_verify_modes("Boot the app"), ["test", "start"])
        self.assertEqual(
            vmp.derive_verify_modes("Refactor against the frozen anchor"), ["anchor"],
        )
        self.assertEqual(vmp.derive_verify_modes("Bump the spring-boot parent"), ["none"])
        self.assertEqual(vmp.derive_verify_modes("Migrate and make it build"), ["compile"])

    def test_none_dropped_from_vocab_proposes_empty(self):
        """An overlay that removed ``none`` must not have the resolver propose it
        — the debt-carry phase falls back to the default full gate ([]) instead."""
        surviving = ("compile", "test", "start", "adversarial", "anchor")
        with patch.object(vmp, "MODE_VOCAB", return_value=surviving):
            self.assertEqual(vmp.derive_verify_modes("Bump the spring-boot parent"), [])

    def test_anchor_dropped_from_vocab_proposes_empty(self):
        """Same guarantee for ``anchor``: removed from the vocab → the
        refactor-with-anchor branch yields [] rather than a stale literal."""
        surviving = ("compile", "test", "start", "adversarial", "none")
        with patch.object(vmp, "MODE_VOCAB", return_value=surviving):
            self.assertEqual(
                vmp.derive_verify_modes("Refactor against the frozen anchor"), [],
            )

    def test_compile_and_start_partial_drop(self):
        """If the vocab drops ONE member of a multi-mode proposal, only the
        survivor is returned (no whole-list collapse). Drop ``start``: the
        boot phase proposes ``["test"]``, not ``["test","start"]`` or ``[]``."""
        with patch.object(vmp, "MODE_VOCAB",
                          return_value=("compile", "test", "adversarial", "anchor", "none")):
            self.assertEqual(vmp.derive_verify_modes("Boot the app"), ["test"])


class DeriveVerifyModesFailOpen(TestCase):
    """derive_verify_modes NEVER raises — [] on any bad input."""

    def test_empty_string(self):
        self.assertEqual(vmp.derive_verify_modes(""), [])

    def test_whitespace_only(self):
        self.assertEqual(vmp.derive_verify_modes("   \t\n  "), [])

    def test_none_input(self):
        self.assertEqual(vmp.derive_verify_modes(None), [])

    def test_non_string_input_does_not_raise(self):
        for bad in (123, [], {}, object()):
            self.assertEqual(vmp.derive_verify_modes(bad), [])


class VerifyModeWhenToUseField(TestCase):
    """The substrate: every baseline mode row carries a ``when_to_use`` (sourced
    from the contract's "When to use" column), and the accessor returns it."""

    def test_every_baseline_mode_has_when_to_use(self):
        for mode in ("compile", "test", "start", "adversarial", "anchor"):
            self.assertTrue(
                vmp.when_to_use_for(mode).strip(),
                f"mode {mode!r} must carry a non-empty when_to_use",
            )

    def test_when_to_use_is_non_empty_for_known_mode(self):
        self.assertIn("compiles", vmp.when_to_use_for("compile").lower())

    def test_unknown_mode_returns_empty_string(self):
        self.assertEqual(vmp.when_to_use_for("does-not-exist"), "")


if __name__ == "__main__":
    main()
