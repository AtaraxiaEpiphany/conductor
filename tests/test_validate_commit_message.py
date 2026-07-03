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
from lib.validation import commit_arg_shell_broken_reason  # noqa: E402


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

    def test_invalid_type_with_scope_keeps_scope(self):
        # `conductor(checkpoint): …` — "conductor" is the plugin name, not a
        # commit type, but the user chose a real scope. Repair the TYPE only;
        # never produce the double-prefixed `fix(scope): conductor(checkpoint): …`
        # that the generic fallback used to emit.
        ok, suggested = validate_commit_message(
            'git commit -m "conductor(checkpoint): Checkpoint end of Phase 1"'
        )
        self.assertFalse(ok)
        self.assertEqual("chore(checkpoint): Checkpoint end of Phase 1", suggested)
        self.assertNotIn("conductor", suggested)
        self.assertNotIn("fix(scope)", suggested)

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

    # --- No-space -m forms: `git commit -m"…"` / `-m'…'` / `-mfoo` previously
    #     bypassed validation entirely (the gate and extractor both required
    #     whitespace after -m), the most common shell shorthand slipping past V10.
    def test_no_space_double_quoted_valid(self):
        self.assertEqual((True, None),
                         validate_commit_message('git commit -m"feat(auth): add login"'))

    def test_no_space_double_quoted_invalid_flagged(self):
        ok, suggested = validate_commit_message('git commit -m"random wip junk"')
        self.assertFalse(ok)
        self.assertEqual("fix(scope): random wip junk", suggested)

    def test_no_space_single_quoted_invalid_flagged(self):
        ok, suggested = validate_commit_message("git commit -m'bad subject line'")
        self.assertFalse(ok)
        self.assertEqual("fix(scope): bad subject line", suggested)

    def test_attached_bare_value_invalid_flagged(self):
        # `-mfoo` (git's short-option attached form) is also caught.
        ok, _ = validate_commit_message('git commit -mrandom_junk_message')
        self.assertFalse(ok)

    def test_word_with_dash_m_is_not_a_flag(self):
        # `-m` inside a filename/word must not be mistaken for the message flag.
        # No real -m here → nothing to validate statically → allowed.
        self.assertEqual((True, None),
                         validate_commit_message('git commit file-m.txt'))


# --- Shell-broken -m arguments: hard-deny signal (not V10's soft ask) -------
# Regression: an orchestrator mis-substitution emitted `git commit -m ()`,
# which bash rejects with "syntax error near unexpected token `('". V10 alone
# only *asks*, so the broken command could still reach the shell. The detector
# returns a deny-reason for an UNQUOTED bare -m token carrying shell-breaking
# metacharacters; quoted values (shell-safe) and dynamic substitutions (handled
# by the allow-through policy) return None.
class CommitArgShellBrokenTests(TestCase):
    def _broken(self, command):
        return commit_arg_shell_broken_reason(command) is not None

    def test_empty_parens_broken(self):
        self.assertTrue(self._broken("git commit -m ()"))

    def test_placeholder_angle_brackets_broken(self):
        self.assertTrue(self._broken("git commit -m <commit_msg>"))

    def test_unquoted_paren_message_broken(self):
        # Unquoted `feat(auth): …` — parens make it a bash syntax error.
        self.assertTrue(self._broken("git commit -m feat(auth): login"))

    def test_quoted_parens_safe(self):
        self.assertFalse(self._broken('git commit -m "feat(auth): add login"'))

    def test_single_quoted_safe(self):
        self.assertFalse(self._broken("git commit -m 'feat(auth): x'"))

    def test_quoted_dynamic_substitution_safe(self):
        # Quoted $(…) is allow-through policy, not a shell-broken deny.
        self.assertFalse(self._broken('git commit -m "$(somecmd)"'))

    def test_bare_word_no_metachar_safe(self):
        # No metacharacter → leaves it to V10 (non-conventional → ask).
        self.assertFalse(self._broken("git commit -mrandom_junk_message"))

    def test_empty_quoted_safe(self):
        self.assertFalse(self._broken('git commit -m ""'))

    def test_no_m_flag_safe(self):
        self.assertFalse(self._broken("git commit file-m.txt"))
        self.assertFalse(self._broken("git commit"))


if __name__ == "__main__":
    main()
