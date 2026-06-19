"""Tests for lib.validation.validate_commit_message.

Regression: when a commit message was supplied via a shell heredoc
(``-m "$(cat <<'EOF' … EOF)"``), the validator read the raw ``$(cat <<'EOF'…``
syntax as the message instead of the literal body. A valid message like
``chore(conductor): …`` was therefore flagged as non-conventional (false
positive), and the "suggested fix" wrapped the entire heredoc blob, producing
garbage like ``fix(scope): $(cat <<'EOF' …``.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
# Production gets scripts/ on sys.path automatically (script dir = sys.path[0]);
# replicate that so `from lib.validation import …` resolves.
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from lib.validation import validate_commit_message  # noqa: E402


# The exact command shape that triggered the original false positive.
HEREDOC_CMD = (
    'git add -A && git diff --cached --quiet || git commit -m "$(cat\n'
    "  <<'EOF'\n"
    "  chore(conductor): Fix state consistency after recovery\n"
    "  Complete P1.T5.1 (add pytest+pytest-cov) — work commit 68e4d17 existed but\n"
    "  dispatch-finalize never ran to record it; recovery detected the post-start\n"
    "  commit and marked the subtask complete.\n"
    "  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
    "  EOF\n"
    '  )"'
)


class ValidateCommitMessageTests(TestCase):
    # --- Plain conventional messages are valid ---
    def test_valid_double_quoted(self):
        self.assertEqual((True, None),
                         validate_commit_message('git commit -m "feat(auth): add login"'))

    def test_valid_single_quoted(self):
        self.assertEqual((True, None),
                         validate_commit_message("git commit -m 'fix(api): handle null'"))

    def test_valid_all_types(self):
        for t in ("feat", "fix", "docs", "style", "refactor", "test", "chore"):
            ok, _ = validate_commit_message(f'git commit -m "{t}(x): does a thing"')
            self.assertTrue(ok, f"{t} should be accepted")

    def test_no_m_flag_is_allowed(self):
        # Editor / -F file path — nothing to validate statically.
        self.assertEqual((True, None), validate_commit_message("git commit"))

    # --- THE REGRESSION: heredoc-built messages must be parsed, not flagged ---
    def test_heredoc_with_valid_subject_is_valid(self):
        ok, suggested = validate_commit_message(HEREDOC_CMD)
        self.assertTrue(ok, "valid conventional message inside a heredoc must not be flagged")
        self.assertIsNone(suggested)

    def test_heredoc_compact_close_eof_paren(self):
        # `EOF)` on one line (closing the $(...) ) must still close the heredoc.
        cmd = 'git commit -m "$(cat <<EOF\nfeat(x): y\nEOF)"'
        self.assertEqual((True, None), validate_commit_message(cmd))

    def test_heredoc_unquoted_delimiter(self):
        cmd = 'git commit -m "$(cat <<EOF\nfix(a): b\nEOF)"'
        self.assertEqual((True, None), validate_commit_message(cmd))

    def test_heredoc_invalid_subject_flagged_with_clean_suggestion(self):
        cmd = 'git commit -m "$(cat <<\'EOF\'\nrandom non-conventional subject\nbody line two\nEOF)"'
        ok, suggested = validate_commit_message(cmd)
        self.assertFalse(ok)
        # Suggestion must be a single conventional line — NOT the multi-line body.
        self.assertIsNotNone(suggested)
        self.assertNotIn("\n", suggested, "suggestion must stay single-line")
        self.assertTrue(suggested.startswith("fix(scope): "))
        self.assertNotIn("EOF", suggested)
        self.assertNotIn("body line two", suggested)

    # --- Command / variable substitution we can't expand is allowed through ---
    def test_command_substitution_is_allowed(self):
        # We cannot know what `somecmd` emits, so blocking would be a false
        # positive. Allow without blocking.
        self.assertEqual((True, None),
                         validate_commit_message('git commit -m "$(somecmd)"'))

    def test_backtick_substitution_is_allowed(self):
        self.assertEqual((True, None),
                         validate_commit_message('git commit -m "`somecmd`"'))

    def test_variable_expansion_is_allowed(self):
        self.assertEqual((True, None),
                         validate_commit_message('git commit -m "$MSG"'))

    # --- Suggestions for genuinely non-conventional subjects ---
    def test_conductor_action_verb_suggests_chore_conductor(self):
        ok, suggested = validate_commit_message('git commit -m "Complete P1.T2 setup"')
        self.assertFalse(ok)
        self.assertEqual("chore(conductor): complete P1.T2 setup", suggested)

    def test_valid_type_missing_scope_repairs_scope_only(self):
        ok, suggested = validate_commit_message('git commit -m "feat: add thing"')
        self.assertFalse(ok)
        self.assertEqual("feat(scope): add thing", suggested)

    def test_generic_subject_wrapped(self):
        ok, suggested = validate_commit_message('git commit -m "random rambling message"')
        self.assertFalse(ok)
        self.assertEqual("fix(scope): random rambling message", suggested)

    # --- Multiple -m: the subject (first -m) is what's validated ---
    def test_multiple_m_flags_validates_subject(self):
        cmd = 'git commit -m "feat(x): y" -m "extended body that is long"'
        self.assertEqual((True, None), validate_commit_message(cmd))

    def test_multiple_m_flags_bad_subject_flagged(self):
        cmd = 'git commit -m "bad subject" -m "feat(x): not the subject"'
        ok, suggested = validate_commit_message(cmd)
        self.assertFalse(ok)
        self.assertEqual("fix(scope): bad subject", suggested)

    # --- Flag-separated commits still work ---
    def test_flags_before_m(self):
        self.assertEqual((True, None),
                         validate_commit_message('git commit --no-verify -m "docs(r): tweak"'))


if __name__ == "__main__":
    main()
