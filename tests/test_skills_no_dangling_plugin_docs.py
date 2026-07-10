r"""Guard: plugin-resident skills/ and agents/ must not reference plugin-internal
``conductor/design/`` docs by a project-relative path.

D1 (seeding bug): ``skills/wiki`` and ``skills/wiki-doctor`` do ``Fetch and
execute `conductor/design/agent-error-handling.md```, and the step skills
``See `conductor/design/rail-b-step.md``` / ``rail-b-wave-step.md``. Those files
live in the PLUGIN's ``conductor/design/`` (the plugin dogfoods itself as a
conductor project), but ``setup`` NEVER copies them into a user project — so a
project-relative ref dangles in every real (non-dogfood) project and the skill
breaks (hard, for the ``Fetch and execute`` gate in wiki/wiki-doctor). The
plugin's own ``conductor/design/`` docs (``agent-error-handling``,
``rail-b-step``, ``rail-b-wave-step``, ``decision-*``) are plugin-internal;
resident skills/agents that execute in a user-project context must reach them via
``${CLAUDE_PLUGIN_ROOT}/conductor/design/...`` (the repo-endorsed remedy in
``test_templates_no_dangling_plugin_docs.py``), never a bare project-relative path.

Discriminator (same as the templates guard): a ``conductor/design/<x>`` ref whose
target EXISTS in the plugin's own ``conductor/design/`` tree is a plugin-internal
doc referenced project-relative → DENY (the defect); a ref whose target does NOT
exist there is a legitimate project-corpus name (``tech-stack.md``,
``architecture/``, ``api-specs/``, ``database/``, a ``decision-*`` the project
grows) → ALLOW. ``${CLAUDE_PLUGIN_ROOT}/``-prefixed refs are correct and excluded.

Scoped to ``skills/`` + ``agents/`` — the plugin-resident surfaces that execute in
a user-project context. In-repo ``conductor/design/*.md`` wikilinks that cross-
reference each other are legitimate same-tree refs and are not scanned here.
"""
import re
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
RESIDENT_DIRS = [REPO / "skills", REPO / "agents"]
PLUGIN_DESIGN = REPO / "conductor" / "design"

# `conductor/design/<rest>` inside [[wikilinks]], markdown links, or bare prose.
# `<rest>` stops at any char outside [A-Za-z0-9_./-] — so `]]`, `)`, backticks,
# spaces, and the `*` in a `decision-*.md` glob all terminate it.
_REF = re.compile(r"conductor/design/([A-Za-z0-9_./\-]+)")
# Correct plugin-rooted refs — masked out first so they are never flagged.
_PLUGIN_ROOTED = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/conductor/design/[A-Za-z0-9_./\-]+")


def _stem(rest: str) -> str:
    """Normalize a captured path fragment to a stem for the existence check:
    drop any ``#anchor``/``|alias`` (defensively), a trailing slash/dot, and a
    ``.md`` suffix so both ``[[x]]`` and ``[t](x.md)`` forms compare alike."""
    rest = re.split(r"[|#]", rest)[0].rstrip("/.")
    if rest.endswith(".md"):
        rest = rest[:-3]
    return rest


def _bare_design_refs(text: str):
    """Yield bare ``conductor/design/<rest>`` refs, EXCLUDING correct
    ``${CLAUDE_PLUGIN_ROOT}/conductor/design/...`` ones (masked out first)."""
    masked = _PLUGIN_ROOTED.sub("", text)
    for m in _REF.finditer(masked):
        yield m.group(1)


class SkillsNoDanglingPluginDocsTests(TestCase):
    def test_no_skill_or_agent_references_a_plugin_internal_design_doc_project_relative(self):
        offenders = []
        for d in RESIDENT_DIRS:
            for f in sorted(d.rglob("*.md")):
                for rest in _bare_design_refs(f.read_text()):
                    stem = _stem(rest)
                    if not stem:
                        continue
                    if (PLUGIN_DESIGN / f"{stem}.md").exists():
                        offenders.append(
                            f"{f.relative_to(REPO)}: conductor/design/{stem}")

        self.assertEqual(
            offenders, [],
            "skills/agents reference plugin-internal conductor/design docs by "
            "project-relative path; these dangle in every non-dogfood project "
            "(setup never copies plugin design docs). Use "
            "${CLAUDE_PLUGIN_ROOT}/conductor/design/... instead. "
            "Offenders:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
