#!/usr/bin/env python3
"""verify-strategy — deterministic invariant checker for a generated strategy.md.

The counterpart to ``scaffold-strategy.py``'s self-verify, but for the
**subagent-generated** path (setup §2.4 step 3, "Generate a project-specific
strategy"). The strategy-writer agent writes ``conductor/workflow/testing/strategy.md``
freely from its live inspection of the project; this script is the deterministic
backstop that asserts the load-bearing contract clauses downstream agents depend on are
present. That keeps an LLM-generated contract doc "verify-don't-generate"-adjacent
(harness-engineering §7.1 L0): the agent tailors, the checker enforces the invariants.

Invariants checked (each a simple substring/regex assert against the written file):

- non-empty file
- a **test-root rule** ("MUST be created under" or a ``test_root`` line)
- a **mirror rule** clause (source→test path mapping)
- a **coverage gate** (``>NN%`` with NN ≥ 80 — the Firewall F3 floor; a lower number
  is rejected, since the generator may only raise or keep the threshold, never lower it)
- an **Existing Convention** clause (scan existing tests before inventing a pattern)

Exit 0 + ``OK:`` line on pass; exit 1 + ``HALT:`` remediation listing every missing
clause on fail.
"""
import argparse
import re
import sys
from pathlib import Path


# Coverage threshold floor — Firewall F3 invariant. The doc may state a higher floor,
# never a lower one.
_COVERAGE_FLOOR = 80
_COVERAGE_RE = re.compile(r"(?:>=?|>)\s*(\d{1,3})\s*%")


def check_invariants(text):
    """Return a list of missing-invariant human descriptions (empty = all present).

    Asserts are intentionally lenient on phrasing (the generator writes prose, not a
    fixed template) but strict on *presence* of each load-bearing concept.
    """
    missing = []
    low = text.lower()

    if not text.strip():
        missing.append("file is empty")
        return missing  # nothing else to check against

    # test-root rule: either the explicit "MUST be created under" phrasing or a
    # `test_root:` / "test root" anchor pointing at a directory.
    if "must be created under" not in low and "test_root" not in low and "test root" not in low:
        missing.append(
            "test-root rule missing — state 'All test files MUST be created under <root>' "
            "or a `test_root:` line"
        )

    # mirror rule: source→test path mapping.
    if "mirror" not in low and "→" not in text and "->" not in low and "maps to" not in low:
        missing.append(
            "mirror rule missing — document the source→test path mapping"
        )

    # coverage gate: a percentage threshold, and it must be ≥ the floor.
    pct_match = _COVERAGE_RE.search(text)
    if not pct_match:
        missing.append(
            f"coverage gate missing — state a '>{_COVERAGE_FLOOR}%' (or higher) threshold"
        )
    else:
        pct = int(pct_match.group(1))
        if pct < _COVERAGE_FLOOR:
            missing.append(
                f"coverage threshold {pct}% is below the {_COVERAGE_FLOOR}% Firewall F3 floor "
                f"— raise it to ≥{_COVERAGE_FLOOR}%"
            )

    # Existing Convention clause: scan existing tests before inventing a pattern.
    if "existing" not in low or "convention" not in low:
        missing.append(
            "Existing Convention clause missing — state that agents scan existing tests "
            "for the established naming/placement before inventing a pattern"
        )

    return missing


def main():
    ap = argparse.ArgumentParser(description="Verify a generated strategy.md's invariants.")
    ap.add_argument("--out", default="conductor/workflow/testing/strategy.md",
                    help="Path to the generated strategy.md to verify")
    args = ap.parse_args()

    target = Path(args.out)
    if not target.exists():
        sys.exit(f"HALT: strategy.md missing: {target} (did the generator run?)")
    try:
        text = target.read_text()
    except OSError as e:
        sys.exit(f"HALT: cannot read {target}: {e}")

    missing = check_invariants(text)
    if missing:
        bullet = "\n  - ".join(missing)
        sys.exit(f"HALT: strategy.md failed invariant check ({target}):\n  - {bullet}")
    print(f"OK: strategy.md invariants verified -> {target}")


if __name__ == "__main__":
    main()
