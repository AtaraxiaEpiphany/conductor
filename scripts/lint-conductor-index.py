#!/usr/bin/env python3
"""lint-conductor-index — live-tree check for a project's ``conductor/index.md``.

The read-strategy map classifies every row with a Status tag (``seeded`` /
``auto`` / ``on-demand``). Only ``seeded`` rows are promised to exist right after
``conductor:setup`` — ``auto`` and ``on-demand`` rows are intentional slots a
skill or the user fills later. This script enforces that promise on the live
project tree: a missing ``seeded`` file is a real defect (setup didn't finish, or
a file was deleted); a missing ``auto`` / ``on-demand`` row is expected and silent.

Invoked by ``/conductor:wiki-doctor`` as the self-healing lever for the
"index.md references files that don't exist" class of bug — it tells the user
exactly which seeded docs are missing instead of leaving every uncreated row
looking like a broken link.

Operates on the project's own ``conductor/index.md`` (resolved from CWD, NOT the
plugin template). Exit 0 + OK line when every ``seeded`` row exists; exit 1 +
remediation listing the missing paths otherwise (mirrors ``scaffold-strategy.py``
exit discipline).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from index_map import seeded_paths


def main():
    ap = argparse.ArgumentParser(description="Lint a project's conductor/index.md seeded rows.")
    ap.add_argument("--root", default=".",
                    help="Project root containing conductor/ (default: CWD).")
    ap.add_argument("--index", default=None,
                    help="Override path to index.md (default: <root>/conductor/index.md).")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    index_path = Path(args.index) if args.index else root / "conductor" / "index.md"
    if not index_path.exists():
        sys.exit(f"HALT: {index_path} not found — run /conductor:setup first.")

    seeded = seeded_paths(index_path.read_text(encoding="utf-8"))
    if not seeded:
        # Not an error per se, but worth surfacing — a seeded-less index means
        # setup's own writes aren't promised anywhere.
        print(f"OK: no seeded rows in {index_path.relative_to(root)} (nothing to verify).")
        return

    missing = sorted(p for p in seeded if not (root / p).exists())
    if missing:
        sys.stderr.write(
            f"{index_path.relative_to(root)} tags these rows 'seeded' but they are missing:\n"
        )
        for p in missing:
            sys.stderr.write(f"  - {p}\n")
        sys.stderr.write(
            "Remediation: re-run /conductor:setup (it seeds these), or fix the row's Status "
            "to auto/on-demand if the doc is genuinely created later.\n"
        )
        sys.exit(1)

    print(f"OK: all {len(seeded)} seeded rows exist under {root.relative_to(Path.cwd()) if root.parent != Path.cwd() else root.name}.")


if __name__ == "__main__":
    main()
