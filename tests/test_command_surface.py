"""Command-surface meta-tests — every registered command actually dispatches.

``COMMAND_GROUPS`` (track_state/commands.py) is the single source for the
grouped help surface AND the set the pre-command guard's sanctioned allowlist
derives from. A command listed there but missing from cli.main()'s dispatch
would be invisible at runtime ("Unknown command") while still LOOKING
sanctioned — the worst kind of drift. These tests walk main()'s dispatch
statically (AST) so the check is exact, not substring-fuzzy.

Pins:
1. Every grouped command reaches a real dispatch site in ``cli.main()``.
2. Every grouped command has a ``COMMAND_HELP`` entry (cmd_help would KeyError).
3. ``SANCTIONED_SUBCOMMANDS`` is EXACTLY groups + {"setup", "help"} — the
   derivation in commands.py can't silently widen or narrow.
4. The pre-command hook's set IS the derived set (not a re-copy).
"""
import ast
import importlib.util
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.commands import (
    COMMAND_GROUPS,
    INDEX_COMMANDS,
    SANCTIONED_SUBCOMMANDS,
)
from scripts.track_state import cli

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "scripts" / "track_state" / "cli.py"


def _load_pcc():
    """pre-command-check.py isn't an importable module (hyphenated name) —
    file-load it, the same idiom test_extract_track_dirs uses."""
    spec = importlib.util.spec_from_file_location(
        "pre_command_check_uut", _REPO / "scripts" / "pre-command-check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pcc = _load_pcc()


def _dispatched_commands() -> set:
    """String constants main() dispatches on, via static AST walk.

    Collects:
      * ``cmd == "literal"`` comparisons (either operand order);
      * ``cmd in ("a", "b")`` membership tuples;
      * ``cmd in _INDEX_COMMANDS`` (resolved from track_state.commands);
      * string keys of dispatch-dict literals (values are cmd_* functions),
        e.g. the _QUERY_FNS ``{"check": cmd_check, ...}`` table.
    """
    tree = ast.parse(_CLI.read_text(encoding="utf-8"))
    main_fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    found = set(INDEX_COMMANDS)  # `cmd in _INDEX_COMMANDS` branch family

    for node in ast.walk(main_fn):
        if isinstance(node, ast.Compare):
            left, ops, comparators = node.left, node.ops, node.comparators
            operands = [left, *comparators]
            is_cmd = any(
                isinstance(o, ast.Name) and o.id == "cmd" for o in operands
            )
            if not is_cmd:
                continue
            for o in operands:
                if isinstance(o, ast.Constant) and isinstance(o.value, str):
                    found.add(o.value)
                # cmd in ("a", "b") — the tuple literal is the comparator.
                if isinstance(o, ast.Tuple):
                    found.update(
                        e.value for e in o.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    )
        # Dispatch dicts: {"resolve-track": cmd_resolve_track, ...} — string
        # keys mapped to cmd_* names are dispatch sites even without a `cmd ==`
        # comparison (the _QUERY_FNS pattern).
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and isinstance(v, ast.Name) and v.id.startswith("cmd_")):
                    found.add(k.value)
    return found


class CommandSurfaceTests(TestCase):
    def test_every_grouped_command_dispatches(self):
        """A command in COMMAND_GROUPS must reach a real main() branch —
        else `track-state <cmd>` prints 'Unknown command' while help and the
        sanctioned allowlist both advertise it."""
        dispatched = _dispatched_commands()
        grouped = {c for _g, cmds in COMMAND_GROUPS for c in cmds}
        missing = sorted(grouped - dispatched)
        self.assertEqual(
            missing, [],
            f"COMMAND_GROUPS lists commands with no dispatch site in "
            f"cli.main(): {missing}")

    def test_every_grouped_command_has_help_entry(self):
        """cmd_help indexes COMMAND_HELP by every grouped command — a missing
        entry is an unhandled KeyError in `track-state help`."""
        grouped = {c for _g, cmds in COMMAND_GROUPS for c in cmds}
        missing = sorted(grouped - set(cli.COMMAND_HELP))
        self.assertEqual(
            missing, [],
            f"COMMAND_HELP missing entries for grouped commands: {missing}")

    def test_sanctioned_set_is_exact_derivation(self):
        """SANCTIONED_SUBCOMMANDS must be EXACTLY groups + hidden setup alias
        + help — the derivation replaces the old hand copy, and any extra or
        missing entry is a security/surface drift."""
        grouped = {c for _g, cmds in COMMAND_GROUPS for c in cmds}
        self.assertEqual(SANCTIONED_SUBCOMMANDS, grouped | {"setup", "help"})

    def test_precommand_hook_uses_derived_set(self):
        """pre-command-check's _SANCTIONED_TS_SUBCOMMANDS must BE the derived
        set (file-loaded from commands.py), not a re-copied literal."""
        self.assertEqual(pcc._SANCTIONED_TS_SUBCOMMANDS, SANCTIONED_SUBCOMMANDS)

    def test_cli_reexports_unchanged_names(self):
        """cli._COMMAND_GROUPS / cli._INDEX_COMMANDS are the commands-module
        objects (historical names tests and skills import from cli)."""
        self.assertIs(cli._COMMAND_GROUPS, COMMAND_GROUPS)
        self.assertIs(cli._INDEX_COMMANDS, INDEX_COMMANDS)


if __name__ == "__main__":
    main()
