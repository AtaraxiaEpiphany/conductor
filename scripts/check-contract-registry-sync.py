#!/usr/bin/env python3
"""check-contract-registry-sync — drift gate for plan-format-contract.md.

The plan-format contract (``runtime/contracts/plan-format-contract.md``) is read
verbatim by ``spec-planner`` (§4.2). Its job is the plan.md **grammar and
invariants** (status markers, subtask rules, deps rules, the ``<!-- verify: -->``
form) — NOT a hand-maintained tag/mode/shape vocabulary. The tag/mode/shape
**vocabulary + semantics** live in the resolved registries
(``task-type-profiles.json``, ``verify-mode-profiles.json``,
``workflow-shapes.json`` — plugin baseline ⊕ project overlay), rendered by
``track-state registry-doc``.

A hand-maintained tag/mode/shape enumeration *table* in the contract would be a
third home for the vocabulary (alongside the registry and the ``[Conductor
Registry]`` block injected into agents) and the first to drift: a project
overlay adds a tag, mode, or shape and the contract silently contradicts it.
This script makes "never drift" a CI guarantee, not a hope — the same discipline
as ``check-plan-annotations``.

What it flags
-------------
A markdown table row (a line beginning with ``|``) whose first cell is a known
tag literal (``[Explore]``, ``[Migrate]``, … — every entry in the resolved
``TAG_VOCAB``), a known mode literal (``compile``, ``anchor``, … — every entry
in the resolved ``MODE_VOCAB``), or a known shape literal (``default``,
``research-first``, … — every entry in the resolved ``SHAPES_VOCAB``). Tag
literals are matched as the bracketed form ``[<Tag>]`` because that is how they
appear in plan.md / the contract; modes and shapes are matched bare
(``compile``, ``default``) as backticked or raw first-cell tokens.

What it does NOT flag
---------------------
Prose mentions of a tag/mode/shape that are NOT a table row — e.g. a Rule keyed
on a specific tag (``Tag it with [Manual] …``), a grammar example
(``- [ ] [Migrate] bump spring-boot``), or the ``<!-- verify: compile -->``
directive examples. Those are legitimate: they are grammar/invariant text, not
a vocab enumeration. Only a tabular enumeration (the data-duplication shape)
trips the gate.

Exit 0 + OK line on success; exit 1 + remediation message on any failure.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from track_state import task_profiles as tp  # noqa: E402
from track_state import verify_mode_profiles as vmp  # noqa: E402
from track_state import workflow_shapes as ws  # noqa: E402


def _first_cell(row):
    """The trimmed first cell of a markdown table row, or None if not a table row.

    A table row is a line beginning with ``|``; the first cell is the text up to
    the next ``|``. Surrounding markdown formatting (backticks, bold) is stripped
    so ``| `[Explore]` |`` and ``| **[Migrate]** |`` compare against the bare
    literal ``[Explore]`` / ``[Migrate]``. Separator rows (``|---|---|``) and the
    header rule are skipped — a separator's first cell is empty or dashes, never
    a tag/mode literal.
    """
    s = row.strip()
    if not s.startswith("|"):
        return None
    inner = s[1:]
    cell = inner.split("|", 1)[0].strip()
    # Strip markdown code-span / emphasis wrappers so the literal compares clean.
    cell = cell.strip("`*")
    return cell


def main():
    contract = get_plugin_root() / "runtime" / "contracts" / "plan-format-contract.md"
    if not contract.exists():
        sys.exit(f"HALT: contract missing: {contract} (is CLAUDE_PLUGIN_ROOT set correctly?)")

    # The resolved vocab (baseline ⊕ overlay). The contract ships in the plugin,
    # so the plugin baseline vocab is the relevant set; but reading the live
    # modules means a project overlay's tag/mode is also recognized (defensive —
    # the contract is plugin-relative, but the modules resolve whatever the env
    # carries).
    tag_literals = {f"[{t}]" for t in tp.TAG_VOCAB()}
    mode_literals = set(vmp.MODE_VOCAB())
    shape_literals = set(ws.SHAPES_VOCAB())

    findings = []
    for lineno, line in enumerate(contract.read_text(encoding="utf-8").splitlines(), 1):
        cell = _first_cell(line)
        if cell is None:
            continue
        if cell in tag_literals:
            findings.append(
                f"  line {lineno}: tag `{cell}` enumerated as a table row — "
                f"tag vocabulary belongs in the registry (`track-state registry-doc`), "
                f"not a hand-maintained table in the contract"
            )
        elif cell in mode_literals:
            findings.append(
                f"  line {lineno}: mode `{cell}` enumerated as a table row — "
                f"mode vocabulary belongs in the registry (`track-state registry-doc`), "
                f"not a hand-maintained table in the contract"
            )
        elif cell in shape_literals:
            findings.append(
                f"  line {lineno}: shape `{cell}` enumerated as a table row — "
                f"shape vocabulary belongs in the registry (`track-state registry-doc`), "
                f"not a hand-maintained table in the contract"
            )

    if findings:
        sys.exit(
            "HALT: plan-format-contract.md duplicates registry data as a hand-maintained "
            "table.\nA tag/mode/shape enumeration in the contract is a third home for the "
            "vocabulary (registry + injected [Conductor Registry] block + this table) "
            "and the first to drift — a project overlay adding a tag/mode/shape would "
            "silently contradict it.\nRemove the table; the resolved vocab is rendered by "
            "`track-state registry-doc` (full tables) / `--tag <Name>` / `--mode <name>` "
            "/ `--shape <name>`, and the [Conductor Registry] block injected into agents "
            "is authoritative at dispatch.\nDuplicated rows:\n" + "\n".join(findings)
        )

    print("OK: plan-format-contract.md carries no hand-maintained tag/mode/shape "
          "enumeration table (vocab is registry-sourced).")


if __name__ == "__main__":
    main()
