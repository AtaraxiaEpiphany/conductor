#!/usr/bin/env python3
"""lint-grill-contract-drift — drift gate for a second, restated grill home.

The grill discipline (four-quadrant stance, one-question-at-a-time grill loop,
premise-challenge pass, operationalize-unknowns, signal-done) is single-homed in
``runtime/contracts/grill-discipline.md``. brief, spec-reviewer, and discover all
adopt it by **Reading that contract on demand and following it** — none restates
the discipline. Per ``runtime/contracts/prose-style.md`` Bucket B, the moment a
second surface restates the discipline in prose, the two homes silently diverge
(one is edited, the other rots). This gate makes that drift a build failure the
instant it appears: a prompt file that **triggers** on the discipline (uses its
signature mechanics) but **does not reference** the single home is flagged.

Trigger mechanics (not the bare word "grill")
---------------------------------------------
The trigger is the discipline's *signature mechanics* —
``four-quadrant`` / ``one question at a time`` — NOT the bare word ``grill``. A
surface that merely *mentions* grilling (``new-track`` saying "run brief for a
grilled shared understanding") routes to the grill; it does not perform it, so it
has no discipline to restate and no home to cite. Flagging it would force a
non-grilling surface to cite a contract it doesn't use. The signature mechanics
identify a surface that actually *restates* the discipline.

Scope: ``agents/``, ``skills/`` (the prompt surface). ``runtime/`` is excluded —
the contract home lives there and would self-flag (it carries every mechanic and,
correctly, no self-reference). **Fenced code blocks** are skipped (a mechanic
named inside a real code example is not a restatement); inline code spans in prose
ARE scanned, because that is where a restated discipline hides.

Exit 0 + OK line on success; exit 1 + remediation message on any finding.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root  # noqa: E402

# The discipline's signature mechanics — a surface using EITHER is restating the
# grill discipline and must cite its single home. Narrowed from a bare \bgrill\b
# (which false-positives on surfaces that merely mention grilling, e.g. new-track).
TRIGGER_RE = re.compile(r"four[-_ ]?quadrant|one question at a time", re.IGNORECASE)

# The single home — stem only, so it matches both the Read-on-demand form
# (${CLAUDE_PLUGIN_ROOT}/runtime/contracts/grill-discipline.md) and the
# [[runtime/contracts/grill-discipline]] wikilink form.
REF_RE = re.compile(r"runtime/contracts/grill-discipline")

FENCE_RE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)

WATCH_DIRS = ("agents", "skills")
WATCH_FILES = ()


def strip_fenced_blocks(text: str) -> str:
    """Blank fenced code blocks (keep line count) so code examples aren't flagged."""
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
    """Yield trigger matches when the text restates the discipline without citing
    its single home. A text that references the contract is clean regardless of
    how many triggers it carries (it follows the home, not restates it)."""
    stripped = strip_fenced_blocks(text)
    if REF_RE.search(stripped):
        return  # cites the single home — not drift
    for m in TRIGGER_RE.finditer(stripped):
        yield m.group(0)


def main() -> int:
    root = get_plugin_root()
    files = list(watched_files(root))
    findings = []
    for path in files:
        for hit in scan_text(path.read_text(encoding="utf-8")):
            findings.append((path.relative_to(root), hit))
    if findings:
        print("grill-contract-drift: grill discipline restated without citing its "
              "single home:")
        for rel, hit in findings:
            print(f"  {rel}: `{hit}`")
        print(
            "\nFix: do NOT restate the grill discipline (four-quadrant stance,\n"
            "one-question-at-a-time loop, premise-challenge, operationalize-unknowns)\n"
            "in prompt prose. Read `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/\n"
            "grill-discipline.md` on demand and FOLLOW it — that contract is the one\n"
            "home; a second restated home silently drifts (prose-style Bucket B).\n"
            "If the surface only MENTIONS grilling (routes to brief, doesn't grill),\n"
            "drop the signature mechanic rather than adding a citation."
        )
        return 1
    print(f"grill-contract-drift: OK ({len(files)} prompt files clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
