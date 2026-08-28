"""Wiring tests for the ``/conductor:adopt-skill`` router — Road A (task type
+ workflow docfile) beside Road B (wrapper agent).

The skill is a thin front door on two generators; these pins hold the door
honest: the frontmatter actually grants the tools Road A needs (Write for the
docfile, Edit/AskUserQuestion for the gate/road asks, Bash for the
generators), the model can reliably distill a skill body into a docfile
(haiku could not), the road choice is asked exactly once, Road A orders the
row write BEFORE the docfile (a failed distillation must leave a safe
fail-open tag, never a dangling workflow_doc), the docfile lands on the
PROJECT-side steps path (the bare plugin-relative form would trip the
dangling-doctrine guard), and Road B's contract is preserved verbatim.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "adopt-skill" / "SKILL.md"
ROUTE = ROOT / "skills" / "route" / "SKILL.md"


class FrontmatterTests(TestCase):
    def setUp(self):
        self.body = SKILL.read_text(encoding="utf-8")
        self.fm = self.body.split("---")[1]

    def test_tools_cover_both_roads(self):
        self.assertIn("allowed-tools: Bash, Read, Write, Edit, AskUserQuestion",
                      self.fm)

    def test_model_is_sonnet(self):
        # haiku could not reliably distill a skill body into a docfile.
        self.assertIn("model: sonnet", self.fm)

    def test_description_names_both_roads(self):
        self.assertIn("task type", self.body)
        self.assertIn("wrapper agent", self.body)


class RoadATests(TestCase):
    def setUp(self):
        self.body = SKILL.read_text(encoding="utf-8")

    def test_road_choice_asked_once(self):
        self.assertIn("CHOOSE THE ROAD", self.body)
        self.assertIn("AskUserQuestion", self.body)

    def test_generates_via_tag_add(self):
        self.assertIn("track-state tag add", self.body)
        self.assertIn("--workflow-doc", self.body)

    def test_row_written_before_docfile(self):
        # Order is load-bearing: the tag resolves fail-open to default TDD
        # while the docfile is missing — a failed distillation leaves a safe
        # tag, never a broken dispatch.
        a2 = self.body.index("A2. WRITE THE ROW (FIRST")
        a3 = self.body.index("A3. DISTILL")
        self.assertLess(a2, a3)
        self.assertIn("fail-open", self.body)

    def test_docfile_written_to_project_steps_path(self):
        self.assertIn("conductor/workflow/steps/<docfile>.md", self.body)

    def test_no_bare_plugin_side_steps_ref(self):
        # The plugin-relative form would trip the dangling-doctrine guard and
        # point an executor at a file it cannot write.
        self.assertNotIn("templates/workflow/steps/", self.body)

    def test_gate_defaults_are_safe(self):
        # Pinned phrase is one contiguous line (the markdown wraps mid-clause).
        self.assertIn("exemptions** (full TDD", self.body)

    def test_halt_on_generator_error(self):
        self.assertIn("print the errors verbatim, then", self.body)


class RoadBPreservedTests(TestCase):
    def setUp(self):
        self.body = SKILL.read_text(encoding="utf-8")

    def test_still_runs_roster_add(self):
        self.assertIn("track-state roster add", self.body)

    def test_halt_on_error_contract_kept(self):
        self.assertIn("ok: false", self.body)
        self.assertIn("Do not attempt to fix the errors yourself", self.body)

    def test_usage_line_kept(self):
        self.assertIn(
            "usage: /conductor:adopt-skill <name> --skill <skill>",
            self.body)


class RouteTableTests(TestCase):
    def setUp(self):
        self.route = ROUTE.read_text(encoding="utf-8")

    def test_row_covers_both_roads(self):
        line = [l for l in self.route.splitlines()
                if "adopt-skill" in l][0]
        self.assertIn("/conductor:adopt-skill", line)
        self.assertIn("task type", line)
        self.assertIn("wrapper agent", line)


if __name__ == "__main__":
    main()
