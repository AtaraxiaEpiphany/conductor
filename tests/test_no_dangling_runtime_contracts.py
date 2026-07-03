r"""Guard: plugin files that resolve at runtime in a project context must reference
runtime/contracts/ docs via ${CLAUDE_PLUGIN_ROOT}, not a bare project-relative path.

``runtime/contracts/*.md`` are plugin-internal behavioral contracts (siblings of
``core-contract.md`` / ``subagent-firewall.md``), never copied into a project by
setup. A bare ``runtime/contracts/foo.md`` ref in an agent / skill / template /
runtime doc resolves **project-relative** once the plugin runs in a foreign
project, where the path does not exist → the agent's Read fails. The
``${CLAUDE_PLUGIN_ROOT}`` prefix resolves to the plugin's own tree at runtime.

This is the symmetric guard to ``test_templates_no_dangling_plugin_docs.py``:
that one covers ``conductor/design/`` plugin-internal docs referenced from
templates; this one covers ``runtime/contracts/`` docs referenced from any
plugin file that executes in a project context.

Wikilinks (``[[runtime/contracts/foo]]``) inside plugin-tree docs that humans
browse (``conductor/design/decision-*.md`` cross-refs, and the contracts' own
intra-``See Also`` links) are tree-relative, not runtime-resolved paths, so they
are correctly bare and are out of scope here — this scan covers only the plugin
files a project-context agent/session reads, not the contracts' own bodies.
"""
import re
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent

# Plugin files read/executed in a project context: a bare runtime/contracts/ ref
# here dangles in a foreign project. templates/ are copied verbatim by setup, so a
# bare ref there dangles too once copied.
_SCAN_FILES = [
    *sorted((REPO / "agents").glob("*.md")),
    *sorted((REPO / "skills").rglob("SKILL.md")),
    *sorted((REPO / "templates").rglob("*.md")),
    REPO / "runtime" / "core-contract.md",
    REPO / "runtime" / "subagent-firewall.md",
]

# `runtime/contracts/<rest>` NOT immediately preceded by ${CLAUDE_PLUGIN_ROOT}/ .
# Fixed-width lookbehind (Python re requires it); <rest> ends at whitespace / backtick / ) / ].
_BARE = re.compile(r"(?<!\$\{CLAUDE_PLUGIN_ROOT\}/)runtime/contracts/[A-Za-z0-9_./\-]+")


class NoDanglingRuntimeContractsTests(TestCase):
    def test_all_runtime_contract_refs_are_plugin_root_prefixed(self):
        offenders = []
        for p in _SCAN_FILES:
            if not p.is_file():
                continue
            for m in _BARE.finditer(p.read_text(encoding="utf-8")):
                offenders.append(f"{p.relative_to(REPO)}: {m.group(0)}")
        self.assertEqual(
            offenders, [],
            "plugin files reference runtime/contracts/ by a bare project-relative "
            "path; these dangle in a foreign project (setup never copies plugin "
            "contracts). Use ${CLAUDE_PLUGIN_ROOT}/runtime/contracts/... . "
            "Offenders:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
