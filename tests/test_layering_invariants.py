r"""Structural layering guard: dependencies flow forward/down only.

Conductor's context layering is directional:

    skills/  →  agents/  →  runtime/contracts/  →  runtime/core-contract.md
    (router)    (workers)   (on-demand specs)       (resident)

Skills are the top routing layer; agents are dispatched *by name*
(``conductor:<name>``), never by path; contracts are loaded *downward* by the
agents/skills that consume them. Nothing below the skills layer should issue a
resolvable load reference (a ``${CLAUDE_PLUGIN_ROOT}/...`` path) *upward* into
``skills/`` or ``agents/``. Concretely:

- **agents/** never ``Read`` another agent's body, and never reach up to ``skills/``
  (an agent has no business loading a skill — skills route to agents, not the reverse).
- **runtime/** (contracts + core-contract + firewall) never load ``skills/`` or
  ``agents/`` by path — contracts are consumed *by* agents/skills, they do not
  invoke them.
- **templates/** (copied verbatim into user projects) never reference plugin
  ``skills/`` or ``agents/`` paths.

This test scans every plugin file that resolves in a project context *except*
``skills/`` (where ``${CLAUDE_PLUGIN_ROOT}/skills/<self>/references/...`` is the
legitimate same-layer progressive-disclosure load) and asserts neither upward
prefix appears. A resolvable upward ref is a real layering violation; a *bare*
``skills/``/``agents/`` token is not (frontmatter ``sources:`` provenance and
prose "consumed by agents/foo.md" notes legitimately name paths without loading
them — so only the ``${CLAUDE_PLUGIN_ROOT}/``-prefixed form is prohibited).

The companion firewall invariant — *which* agents may hold the ``Agent`` tool
(the nested-dispatch allowlist) — is already pinned by
``test_log_checker_wiring.py::test_only_allowlisted_agents_have_agent_tool`` and
is intentionally NOT re-asserted here.
"""
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent

# Everything below the skills layer that resolves in a project context. skills/
# is excluded: a skill router Reading its own references/*.md via
# ${CLAUDE_PLUGIN_ROOT}/skills/<self>/references/... is the legitimate same-layer
# progressive-disclosure load, not an upward reach.
_SCAN_FILES = [
    *sorted((REPO / "agents").glob("*.md")),
    *sorted((REPO / "runtime").rglob("*.md")),
    *sorted((REPO / "templates").rglob("*.md")),
]

# A resolvable load reference UP into the skills/ or agents/ layer. Only this
# prefixed form is a violation — bare "skills/"/"agents/" tokens are legitimate
# provenance/prose and are out of scope.
_FORBIDDEN_UPWARD = (
    "${CLAUDE_PLUGIN_ROOT}/skills/",
    "${CLAUDE_PLUGIN_ROOT}/agents/",
)


class LayeringDirectionTests(TestCase):
    def test_no_upward_load_into_skills_or_agents(self):
        offenders = []
        for p in _SCAN_FILES:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            for needle in _FORBIDDEN_UPWARD:
                if needle in text:
                    offenders.append(f"{p.relative_to(REPO)}: {needle}")
        self.assertEqual(
            offenders, [],
            "a plugin file below the skills layer issues a resolvable load "
            "reference UPWARD into skills/ or agents/ — a layering violation. "
            "Agents are dispatched by name (conductor:<name>), and contracts are "
            "loaded downward by their consumers; nothing below skills/ should "
            "${CLAUDE_PLUGIN_ROOT}-load skills/ or agents/. "
            "Offenders:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    main()
