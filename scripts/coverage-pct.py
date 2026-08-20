#!/usr/bin/env python3
"""coverage-pct - parse a coverage percentage from captured test/coverage output.

Reads tool stdout from **stdin** (the command-digester agent, PURPOSE=red|coverage,
already ran the test+coverage command and captured its output) and prints the
parsed coverage percentage, or ``N/A`` when it cannot be parsed.

Why this exists: the ``command-digester`` agent is a haiku leaf whose job is to
keep verbose pytest/cargo/go-test output **out** of ``task-executor``'s context.
Coverage parsing must stay deterministic and shared with the server-side F3
probe (``on-batch-complete.py``), so the digester pipes captured output here
rather than re-describing the per-language regex in agent prose (which drifts
and is unreliable on a small model).

Usage::

    <test+coverage command> 2>&1 | python3 scripts/coverage-pct.py [--lang auto|python|node|go]

Exit code is always 0 (a parse miss is ``N/A`` on stdout, not an error); the
digester treats ``N/A`` as "coverage not parsed" and reports it honestly rather
than fabricating a number.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.coverage import detect_project_type, parse_coverage_percent  # noqa: E402


def _resolve_lang(arg: str) -> str:
    if arg and arg != "auto":
        return arg
    detected = detect_project_type(Path.cwd())
    return detected or ""


def main() -> None:
    lang = "auto"
    args = sys.argv[1:]
    if "--lang" in args:
        i = args.index("--lang")
        if i + 1 < len(args):
            lang = args[i + 1]

    project_type = _resolve_lang(lang)
    output = sys.stdin.read()
    pct = parse_coverage_percent(output, project_type if project_type else None)
    # {:g} drops the trailing .0 so "94.0" prints as "94" — the digester passes
    # this straight to `track-state write-result --coverage-pct <int>`.
    print("N/A" if pct is None else f"{pct:g}")


if __name__ == "__main__":
    main()
