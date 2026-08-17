"""Marker-map equality lint (campaign 2.6).

The status→checkbox-marker vocabulary is rendered in FOUR places; before this
lint nothing held them together, and the renderings drifted (the status skill
grew a dead task-level ``archived``→``[x]`` row — ``archived`` is TRACK-level
only; the task enum has 8 statuses):

1. ``MARKER_MAP`` — ``scripts/track_state/constants.py`` (the code owner;
   drives plan-sync writes),
2. the **Task State Model** table in ``runtime/core-contract.md``,
3. the render map in ``skills/status/SKILL.md`` §2.0,
4. the mapping line in ``agents/code-reviewer.md``.

All four must agree on BOTH the status set and the marker letter per status.
Each parser here is deliberately tiny — it reads only its own file's rendering
shape, so a formatting change to any one site fails this test rather than
silently orphaning it (the parse regex is the admission token: if you reword a
rendering, you must update its parser, which forces you to look at this lint).
"""
import re
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.constants import MARKER_MAP

ROOT = Path(__file__).resolve().parent.parent

# One regex per rendering shape:
RE_CONTRACT_ROW = re.compile(r"^\|\s*`\[(.)\]`\s*\|\s*(\w+)\s*\|", re.M)
RE_STATUS_SKILL = re.compile(r"`(\w+)`→`\[(.)\]`")
RE_REVIEWER = re.compile(r"`(\w+)=\[(.)\]`")


def _contract_map():
    text = (ROOT / "runtime" / "core-contract.md").read_text(encoding="utf-8")
    start = text.index("## Task State Model")
    end = text.index("##", start + 1)
    return {status: marker for marker, status
            in RE_CONTRACT_ROW.findall(text[start:end])}


def _status_skill_map():
    text = (ROOT / "skills" / "status" / "SKILL.md").read_text(encoding="utf-8")
    return {status: marker
            for status, marker in RE_STATUS_SKILL.findall(text)}


def _reviewer_map():
    text = (ROOT / "agents" / "code-reviewer.md").read_text(encoding="utf-8")
    return {status: marker for status, marker in RE_REVIEWER.findall(text)}


class MarkerMapSyncTests(TestCase):
    def test_all_four_renderings_agree(self):
        renderings = {
            "MARKER_MAP (code)": dict(MARKER_MAP),
            "core-contract Task State Model": _contract_map(),
            "skills/status render map": _status_skill_map(),
            "agents/code-reviewer mapping": _reviewer_map(),
        }
        for name, mapping in renderings.items():
            self.assertTrue(mapping, f"{name} parsed to nothing — parser rot?")
        for name, mapping in renderings.items():
            conflicts = sorted(
                f"{s}: {mapping[s]!r}!={MARKER_MAP[s]!r}"
                for s in set(mapping) & set(MARKER_MAP)
                if mapping[s] != MARKER_MAP[s])
            self.assertEqual(
                mapping, dict(MARKER_MAP),
                f"{name} disagrees with MARKER_MAP: "
                f"only-there={sorted(set(mapping) - set(MARKER_MAP))} "
                f"missing={sorted(set(MARKER_MAP) - set(mapping))} "
                f"letter-conflicts={conflicts}")

    def test_exactly_eight_task_statuses(self):
        # The task enum is 8 statuses. `archived` is TRACK-level only — a
        # 9th row here is the exact dead-row bug this lint retired.
        self.assertEqual(len(MARKER_MAP), 8)
        self.assertNotIn("archived", MARKER_MAP)

    def test_letters_unique(self):
        # Two statuses sharing a letter would make plan.md unparseable.
        letters = list(MARKER_MAP.values())
        self.assertEqual(len(letters), len(set(letters)))


if __name__ == "__main__":
    main()
