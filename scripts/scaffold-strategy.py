#!/usr/bin/env python3
"""scaffold-strategy — write conductor/workflow/testing/strategy.md from the template.

Promotes the setup §2.4 step-3 invariant into code: read the testing-strategy
template, substitute {TEST_ROOT} with the resolved test root, write the target
byte-exact modulo the token, and self-verify. The orchestrator cannot skip or
drift it (harness-engineering §4.4 promote-into-code; §7.2 verify-don't-generate).

Test-root resolution order: --test-root flag, else analysis.json
``structure.test_dirs[0]`` (trailing ``/`` stripped), else ``tests`` (greenfield).

Exit 0 + OK line on success; exit 1 + remediation message on any failure (§4.3).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root


def resolve_root(analysis_path, override):
    """analysis.json structure.test_dirs[0] (trailing '/' stripped), else override, else 'tests'."""
    if override:
        return override.rstrip("/") or "tests"
    if analysis_path.exists():
        try:
            data = json.loads(analysis_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            sys.exit(f"HALT: analysis.json unreadable ({e}) -- pass --test-root")
        test_dirs = (data.get("structure", {}) or {}).get("test_dirs") or []
        if test_dirs and test_dirs[0]:
            return str(test_dirs[0]).rstrip("/")
    return "tests"


def main():
    plugin_root = get_plugin_root()
    ap = argparse.ArgumentParser(description="Scaffold testing/strategy.md from the template.")
    ap.add_argument("--template", default=str(plugin_root / "templates" / "testing" / "strategy.md"))
    ap.add_argument("--out", default="conductor/workflow/testing/strategy.md")
    ap.add_argument("--analysis", default="conductor/.conductor/analysis.json")
    ap.add_argument("--test-root", default=None,
                    help="Override; else analysis.json structure.test_dirs[0], else 'tests'")
    args = ap.parse_args()

    template = Path(args.template)
    if not template.exists():
        sys.exit(f"HALT: template missing: {template} (is CLAUDE_PLUGIN_ROOT set correctly?)")
    root = resolve_root(Path(args.analysis), args.test_root)

    try:
        text = template.read_text()
    except OSError as e:
        sys.exit(f"HALT: cannot read template {template}: {e}")

    n = text.count("{TEST_ROOT}")
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text.replace("{TEST_ROOT}", root))
    except OSError as e:
        sys.exit(f"HALT: cannot write {target}: {e} -- check path/permissions")

    # Self-verify (§7.1 L0): the token must be gone in the written file.
    if "{TEST_ROOT}" in target.read_text():
        sys.exit(f"HALT: {{TEST_ROOT}} still present in {target} after substitution")
    print(f"OK: strategy.md root={root} ({n} tokens substituted) -> {target}")


if __name__ == "__main__":
    main()
