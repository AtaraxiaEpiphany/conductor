"""Tests for ``lib.dispatch_lifecycle.session_token`` — the dispatch-lifecycle
join key.

The join key is what lets a grep group one dispatch's ``probe``/``start``/``stop``
events together. The load-bearing case is the **real-session empty payload**:
both ``session_id`` and ``transcript_path`` arrive empty on every event, so the
fallback to the caller's track dir is the only thing that makes the trail
joinable. These tests pin that fallback chain.
"""
import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from lib import dispatch_lifecycle as lifecycle  # noqa: E402
from unittest import TestCase, main


class SessionTokenTest(TestCase):
    def test_session_id_wins_when_present(self):
        tok = lifecycle.session_token({"session_id": "abc-123"})
        self.assertEqual(tok, "abc-123")

    def test_session_id_beats_transcript_and_fallback(self):
        tok = lifecycle.session_token(
            {"session_id": "abc", "transcript_path": "/x/def.jsonl"},
            fallback="/track/dir",
        )
        self.assertEqual(tok, "abc")

    def test_transcript_stem_when_session_id_empty(self):
        tok = lifecycle.session_token(
            {"session_id": "", "transcript_path": "/proj/uuid-stem.jsonl"},
        )
        self.assertEqual(tok, "uuid-stem")

    def test_fallback_when_both_empty(self):
        """The real-relapse case: both documented fields absent on the payload."""
        tok = lifecycle.session_token({"session_id": "", "transcript_path": ""},
                                      fallback="/tmp/track")
        self.assertEqual(tok, "/tmp/track")

    def test_fallback_when_payload_has_neither_key(self):
        tok = lifecycle.session_token({}, fallback="/tmp/track")
        self.assertEqual(tok, "/tmp/track")

    def test_dash_when_all_empty_and_no_fallback(self):
        self.assertEqual(lifecycle.session_token({}), "-")
        self.assertEqual(lifecycle.session_token(None), "-")

    def test_whitespace_only_session_id_falls_through(self):
        tok = lifecycle.session_token({"session_id": "   "}, fallback="/t")
        self.assertEqual(tok, "/t")

    def test_empty_fallback_falls_to_dash(self):
        # fallback="" must not satisfy the chain — it is falsy.
        tok = lifecycle.session_token({"session_id": "", "transcript_path": ""},
                                      fallback="")
        self.assertEqual(tok, "-")


if __name__ == "__main__":
    main()
