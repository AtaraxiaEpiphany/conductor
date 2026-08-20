"""Wiring tests for the /conductor:route skill.

route is a thin intent-to-command router. Its one load-bearing contract: **the
router must not lie** — every command in the roster must appear in its table
(derived by globbing ``skills/``, not a hardcoded list, so a newly added skill
the router forgets fails the build), the roster's single source is the README
generated commands table, and the frontmatter keeps the cheap-lookup shape
(haiku, Read + AskUserQuestion only — a router has no business editing).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = "skills/route/SKILL.md"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _frontmatter_line(txt, prefix):
    for line in txt.splitlines():
        if line.startswith(prefix):
            return line
    return ""


class RouteFrontmatterTests(TestCase):
    def test_frontmatter_shape(self):
        txt = _read(SKILL)
        self.assertIn("name: route", txt)
        self.assertIn("argument-hint: \"[intent]\"", txt)
        self.assertIn("when_to_use:", txt)
        self.assertIn("model: haiku", txt)  # cheap lookup (dashboard precedent)

    def test_allowed_tools_are_read_only(self):
        # A router resolves and asks; it must not edit, write, or dispatch.
        at = _frontmatter_line(_read(SKILL), "allowed-tools:")
        self.assertIn("Read", at)
        self.assertIn("AskUserQuestion", at)
        for banned in ("Edit", "Write", "Bash", "Agent"):
            self.assertNotIn(banned, at, f"route must stay read-only: {banned}")

    def test_description_leads_with_capability(self):
        # MP rule: front-load the capability word — "Route …", not "You can…".
        desc = _frontmatter_line(_read(SKILL), "description:")
        self.assertTrue(desc.startswith("description: Route"), desc)


class RouteRosterTests(TestCase):
    def test_every_skill_except_route_appears_in_body(self):
        """A router that omits a command is worse than none. Derive the roster
        by globbing skills/*/SKILL.md (NOT a hardcoded list) and assert each
        skill except route itself appears as a `/conductor:<name>` mention in
        the router body — adding a skill without routing it breaks the build."""
        body = _read(SKILL)
        names = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
        self.assertIn("route", names)  # sanity: the router itself exists
        missing = [n for n in names
                   if n != "route" and f"/conductor:{n}" not in body]
        self.assertEqual(
            missing, [],
            "route body does not route to: " + ", ".join(missing))

    def test_cites_readme_generated_table_as_roster_source(self):
        # Single-source rule: README's generated commands table is the roster's
        # home; route defers to it rather than claiming to BE the roster.
        txt = _read(SKILL)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/README.md", txt)
        self.assertIn("commands table", txt)

    def test_does_not_autochain_into_routed_command(self):
        # Mirrors brief/discover's no-autochain contract: print the command,
        # the user runs it.
        txt = _read(SKILL)
        self.assertIn("auto-chain", txt)


if __name__ == "__main__":
    main()
