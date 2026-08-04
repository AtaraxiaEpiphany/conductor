#!/usr/bin/env python3
"""lint-prose-impl-leak — drift gate for rotting implementation citations in markdown prose.

These markdown files are prompts: agents/, skills/, runtime/ are injected into
LLM context, and conductor/ + README.md are read by humans maintaining the
plugin. Both audiences pay for every token / every word of noise. One repeat
offender is the **line-number citation** — ``path/to/file.<ext>:NN`` in prose
(e.g. ``git_ops.py:183``, ``spec_parse.py:16-19``). It is guaranteed to drift:
insert one line above and the citation silently points at the wrong code. Both
live examples this was built from were ALREADY stale (``git_ops.py:183`` cited a
function now at line 173; ``sync.py:42-67`` a function now elsewhere). The fix
is always to cite the STABLE referent — the file (``git_ops.py``) or, better, the
symbol (``_git_commit`` in ``git_ops.py``) — which a reader can grep for
regardless of where code moves.

Scope: ``agents/``, ``skills/``, ``runtime/``, ``conductor/``, top-level
``README.md``. **Fenced code blocks** (``` ... ```) are skipped — a
``.py:NN`` inside a real command or URL is not a prose citation. **Inline code
spans in prose ARE scanned**, because that is precisely where rotting citations
hide (`` `sync.py:42-67` ``).

What this gate deliberately does NOT flag
-----------------------------------------
Bare tool names (``the Write tool``, ``the Agent tool``). These are *sometimes*
load-bearing — when they name a CONSTRAINT the harness enforces (e.g. "you have
no Write tool; use ``track-state write-result``", or "the ``Agent`` tool is
fenced to two dispatch kinds" — the ``PreToolUse:Agent`` hook matches that exact
token). Distinguishing a real constraint from redundant mechanism-as-verb ("Use
the Write tool to write X") is a judgment call a regex gets wrong, so it is left
to review + the prose-style guidance, not this gate.

Exit 0 + OK line on success; exit 1 + remediation message on any finding.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root  # noqa: E402

# Code-file extensions whose ``:NN`` is a rotting citation, never a port/URL.
CODE_EXTS = (
    "py", "js", "mjs", "cjs", "ts", "jsx", "tsx",
    "go", "rs", "rb", "java", "kt", "scala",
    "c", "h", "cpp", "hpp", "cc", "sh", "sql",
)

# ``path/file.ext:NN`` or ``path/file.ext:NN-NN`` — the .ext is a code language.
LINE_NUM_RE = re.compile(
    rf"\b[\w/.-]+\.(?:{'|'.join(CODE_EXTS)}):\d+(?:-\d+)?"
)

FENCE_RE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)

WATCH_DIRS = ("agents", "skills", "runtime", "conductor")
WATCH_FILES = ("README.md",)


def strip_fenced_blocks(text: str) -> str:
    """Blank fenced code blocks (keep line count) so commands/URLs aren't flagged."""
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
        else:
            out.append("" if in_fence else line)
    return "\n".join(out)


def watched_files(root: Path):
    for d in WATCH_DIRS:
        if (root / d).is_dir():
            yield from (root / d).rglob("*.md")
    for f in WATCH_FILES:
        if (root / f).exists():
            yield root / f


def scan_text(text: str):
    """Yield (match_text) for every line-number citation outside fenced blocks."""
    for m in LINE_NUM_RE.finditer(strip_fenced_blocks(text)):
        yield m.group(0)


def main() -> int:
    root = get_plugin_root()
    files = list(watched_files(root))
    findings = []
    for path in files:
        for hit in scan_text(path.read_text(encoding="utf-8")):
            findings.append((path.relative_to(root), hit))
    if findings:
        print("prose-impl-leak: rotting line-number citation(s) in markdown prose:")
        for rel, hit in findings:
            print(f"  {rel}: `{hit}`")
        print(
            "\nFix: drop the `:NN` suffix. Cite the STABLE referent instead — the\n"
            "file (`git_ops.py`) or the symbol (`_git_commit` in `git_ops.py`),\n"
            "which a reader can grep for regardless of where code moves. A line\n"
            "number is stale the moment a line is inserted above it.\n"
            "Full keep/cut rules: runtime/contracts/prose-style.md."
        )
        return 1
    print(f"prose-impl-leak: OK ({len(files)} markdown files clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
