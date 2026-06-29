r"""Guard: copied templates must not reference plugin-internal conductor/design docs
by a project-relative path.

``skills/setup/SKILL.md`` §2.4 copies ``templates/**/*.md`` verbatim into the
scaffolded project (e.g. ``post-loop.md`` → ``conductor/workflow/post-loop.md``).
A ``[[wikilink]]`` or bare ``conductor/design/...`` path written in a template
resolves **project-relative** once copied, but the plugin-internal design docs
(``decision-*``, ``doc-conventions``, ``doc-routing``, ``plan-format-contract``,
``wiki-setup-check``, ``agent-error-handling`` …) are never copied into the
project — so such a reference dangles, and ``doc-linter`` §4.1 (Orphan
References) reports it as a broken wikilink on every wiki-lint pass.

Discriminator — filesystem existence in the plugin's OWN ``conductor/design/``:

- a ``conductor/design/<x>`` ref whose target EXISTS in the plugin tree is a
  plugin-internal doc being referenced project-relative from a COPIED template
  → DENY (the defect);
- a ref whose target does NOT exist in the plugin tree is a legitimate
  project-corpus name (``tech-stack.md``, ``architecture/``, ``api-specs/``,
  ``database/``, or a ``decision-*`` glob the project grows over time) → ALLOW.

This keeps the guard self-maintaining: new plugin design docs are covered
automatically; project-corpus names are never false-flagged. Scoped to
``templates/`` only — in-repo ``conductor/design/*.md`` wikilinks that cross-
reference each other are legitimate same-tree refs and must not be flagged.
"""
import re
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
PLUGIN_DESIGN = REPO / "conductor" / "design"

# `conductor/design/<rest>` inside [[wikilinks]], markdown links, or bare prose.
# `<rest>` stops at any char outside [A-Za-z0-9_./-] — so `]]`, `)`, backticks,
# spaces, and crucially the `*` in a `decision-*.md` glob all terminate it.
_REF = re.compile(r"conductor/design/([A-Za-z0-9_./\-]+)")


def _stem(rest: str) -> str:
    """Normalize a captured path fragment to a stem for the existence check:
    drop any ``#anchor``/``|alias`` (defensively), a trailing slash/dot, and a
    ``.md`` suffix so both ``[[x]]`` and ``[t](x.md)`` forms compare alike."""
    rest = re.split(r"[|#]", rest)[0].rstrip("/.")
    if rest.endswith(".md"):
        rest = rest[:-3]
    return rest


class TemplatesNoDanglingPluginDocsTests(TestCase):
    def test_no_template_references_a_plugin_internal_design_doc(self):
        offenders = []
        for tpl in sorted(TEMPLATES.rglob("*.md")):
            for m in _REF.finditer(tpl.read_text()):
                stem = _stem(m.group(1))
                if not stem:
                    continue
                if (PLUGIN_DESIGN / f"{stem}.md").exists():
                    offenders.append(
                        f"{tpl.relative_to(REPO)}: conductor/design/{stem}")

        self.assertEqual(
            offenders, [],
            "templates/ reference plugin-internal conductor/design docs by "
            "project-relative path; these dangle once setup copies the template "
            "into a project (setup never copies plugin design docs). Use "
            "${CLAUDE_PLUGIN_ROOT}/conductor/design/... or drop the ref. "
            "Offenders:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
