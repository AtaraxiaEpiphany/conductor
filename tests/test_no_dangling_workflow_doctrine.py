r"""Guard: the 3 workflow-doctrine templates must be referenced from
${CLAUDE_PLUGIN_ROOT}/templates/, never copied into a project or read by a bare
project-relative path.

``task-workflow.md``, ``phase-checkpoint.md``, ``post-loop.md`` are plugin
doctrine (the protocol an executor/phase-checker/post-loop follow). setup used
to ``cp`` them verbatim into every project's ``conductor/workflow/``; agents then
read them back from that project-local path — a second home that drifted from
``templates/`` and never auto-updated with the plugin. They are now read
straight from ``${CLAUDE_PLUGIN_ROOT}/templates/<name>.md`` at runtime, exactly
how ``runtime/contracts/*.md`` are consumed (one home, drift-proof).

Two offender forms, both dangle once the plugin runs in a foreign project:

- **old copied form** ``conductor/workflow/<name>.md`` — the path is never valid
  post-D1 (setup no longer copies the files), so it is ALWAYS an offender.
- **bare template form** ``templates/<name>.md`` WITHOUT the
  ``${CLAUDE_PLUGIN_ROOT}/`` prefix — resolves project-relative in a foreign
  project where ``templates/`` does not exist → the agent's Read fails. The
  prefix resolves to the plugin's own tree at runtime (direct analogue of
  ``test_no_dangling_runtime_contracts.py``).

Known non-offenders that the regexes correctly skip:

- ``templates/task-workflow.md:22`` carries a bare ``./phase-checkpoint.md``
  cross-ref. Decision 4 left it untouched (rewriting would inject a token into a
  deliberately zero-token file the executor never follows). It matches NEITHER
  regex — ``./phase-checkpoint.md`` has no ``conductor/workflow/`` and no
  ``templates/`` prefix — so this guard stays green. Cosmetic stale, by design.
- setup/SKILL.md's ``cp ${CLAUDE_PLUGIN_ROOT}/templates/{dev-commands,
  code-styleguides}/`` sources are prefixed AND non-doctrine.

Inherent gap (same as the runtime-contracts guard): a brace-expansion ``cp
"${CLAUDE_PLUGIN_ROOT}/templates/"{task-workflow,...}.md`` is text-scan-opaque.
This is moot post-D1 (no such cp remains), and a regression there is caught by
``test_extract_track_dirs``'s clean-tree assertions, not this text guard.
"""
import re
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent

# Plugin files read/executed in a project context: a bare or conductor/workflow
# doctrine ref here dangles in a foreign project. templates/ are no longer copied
# by setup, so a bare templates ref there dangles too. scripts/on-compact.py is
# the one .py that injects a doctrine path into the main session, so it is
# scanned alongside the markdown consumers.
_SCAN_FILES = [
    *sorted((REPO / "agents").glob("*.md")),
    *sorted((REPO / "skills").rglob("SKILL.md")),
    *sorted((REPO / "templates").rglob("*.md")),
    REPO / "runtime" / "core-contract.md",
    REPO / "runtime" / "subagent-firewall.md",
    REPO / "scripts" / "on-compact.py",
]

# The old copied path — setup no longer creates these, so the path is never
# valid post-D1: always an offender.
_OLD_FORM = re.compile(
    r"conductor/workflow/(?:task-workflow|phase-checkpoint|post-loop)\.md")

# `templates/<name>.md` for one of the 3 doctrine files NOT immediately preceded
# by ${CLAUDE_PLUGIN_ROOT}/ . Fixed-width lookbehind (Python re requires it).
_BARE_TEMPLATE = re.compile(
    r"(?<!\$\{CLAUDE_PLUGIN_ROOT\}/)"
    r"templates/(?:task-workflow|phase-checkpoint|post-loop)\.md")


class NoDanglingWorkflowDoctrineTests(TestCase):
    def test_workflow_doctrine_refs_are_plugin_root_prefixed(self):
        offenders = []
        for p in _SCAN_FILES:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            for m in _OLD_FORM.finditer(text):
                offenders.append(f"{p.relative_to(REPO)}: OLD {m.group(0)}")
            for m in _BARE_TEMPLATE.finditer(text):
                offenders.append(f"{p.relative_to(REPO)}: BARE {m.group(0)}")
        self.assertEqual(
            offenders, [],
            "the 3 workflow-doctrine templates (task-workflow/phase-checkpoint/"
            "post-loop) must be referenced via ${CLAUDE_PLUGIN_ROOT}/templates/... "
            "— setup no longer copies them into conductor/workflow/, and a bare "
            "templates/ or conductor/workflow/ ref dangles in a foreign project. "
            "Offenders:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
