#!/usr/bin/env python3
"""PreToolUse:Write|Edit|MultiEdit guard — protect the frozen anchor set.

The problem this solves
-----------------------
Conductor's F3 coverage gate is a Goodhart surface. ``coverage_pct`` is
self-reported (``task-executor`` runs the suite, parses coverage via
``coverage-pct.py``, writes ``evidence.coverage_pct``), and the measured AC
twin (``ac_verification_measured_rate``, ``spec_integrity.py``) verifies a
test **named** ``test_TC_{n}_{m}`` *exists* — not that it asserts what the AC
requires. So coverage can be driven to 80% by weakening, skipping, or
deleting the very tests that anchor a criterion. No hook guards test files
today: the F2 gate (``pre-command-check.py``) only checks a test file is
*staged* in a feat/fix commit; it cannot see a removed ``assert``, a
``@pytest.mark.skip``, or a deletion in any other commit type.

The frozen anchor (``<track_dir>/.conductor/feature-list.json`` — the
Anthropic ``feature_list.json`` pattern adapted to Conductor's TC convention)
is the exogenous reference the optimizer is forbidden to rewrite: each
feature pins an ``assertion_contract`` and a ``test_locator``. This hook makes
"the executor cannot weaken the anchor set" **deterministic** rather than
prose. It is the graph-engineering "frozen node" principle (a loop the
optimizer would be tempted to weaken, frozen on purpose) promoted from
documentation into code — the same class of prose-invariant-a-model-ignores
gap that ``on-category-write-guard.py`` and ``on-dispatch-dedupe.py`` close.

What it denies
--------------
Two distinct protections, both scoped to a track that has frozen a list:

1. **Anchor-file integrity.** A Write/Edit/MultiEdit whose target *is*
   ``feature-list.json`` is denied — the file is frozen; only the owning
   ``track-state freeze`` / ``track-state thaw`` commands amend it. (The deny
   reason names the command, per the dispatch-dedupe loop lesson: a reason
   that fails to name the recovery re-triggers the same tool and loops.)

2. **Frozen-test weakening.** An Edit/MultiEdit to a file or function named
   by a frozen ``test_locator`` is denied when the diff carries a weakening
   signal — a removed assertion line, or a newly-added skip marker. Adding
   assertions / fixing a test to pass is always allowed; the guard catches
   *regressions in strength*, never *improvements*. A deliberate thaw goes
   through ``track-state thaw`` (the audit path), not the editor.

Design posture — conservative, fail-open
-----------------------------------------
Weakening detection is deliberately a coarse, low-false-positive *signal*
scan, not a semantic diff. It looks for:

  - a line present in ``old_string`` but absent from ``new_string`` that
    matches an assertion marker (``assert``, ``expect(``, ``require(``,
    ``should.``, ``Must``, language-aware); AND/OR
  - a skip marker (``@pytest.mark.skip``, ``@pytest.mark.skipif``,
    ``@Ignore``, ``it.skip(``, ``test.skip(``, ``xit(``, ``// SKIPPED``)
    newly added in ``new_string``.

Anything ambiguous → allow. A misbehaving guard is worse than none: every
path-resolution, I/O, JSON, or parsing error → allow + stderr warning
(mirrors ``on-category-write-guard.py``'s fail-open contract). No frozen
list → allow (the guard is inert on tracks that never opted in).

Scope
-----
Only ``Write`` / ``Edit`` / ``MultiEdit`` are gated. The protected writer IS
the subagent (task-executor) — so, like ``on-category-write-guard.py``, we do
NOT gate on ``agent_type`` presence: the guard applies to every edit
regardless of who issues it. Non-anchor, non-frozen-locator targets pass
straight through.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output  # noqa: E402
from lib.logging import init_logging, log_entry  # noqa: E402
from lib.locked_task import resolve as resolve_locked_task  # noqa: E402
from lib.hook_paths import resolve_rel_target  # noqa: E402

# --- Anchor file name (sidecar sibling of parallel.json / review-seen.json) ---
ANCHOR_FILENAME = "feature-list.json"

# --- Skip markers: a frozen test that gains one of these is being silenced. ---
# ``re.escape``-free by design — these are literal substrings matched
# case-sensitively against the *new* text only.
SKIP_MARKERS = (
    "@pytest.mark.skip",
    "@pytest.mark.skipif",
    "pytest.skip(",
    "@Ignore",
    "@Disabled",
    "it.skip(",
    "test.skip(",
    "xit(",
    "xdescribe(",
    "// SKIPPED",
    "/* SKIPPED */",
)

# --- Assertion markers: a frozen test that loses one of these is weakening. ---
# Matched against full lines in old_string that vanish from new_string. The
# leading whitespace/indentation is tolerated by anchoring on a word boundary
# anywhere in the line; we require the marker to be the *start* of the
# statement (after optional whitespace) to avoid flagging a comment that
# merely mentions "assert".
_ASSERT_LINE = re.compile(
    r"""
    ^ \s*                       # leading indentation
    (?:                         # one assertion statement starter:
        assert\b                #   python / rust / c / go
      | expect\s*\(             #   jest / vitest
      | require\.              #   mocha/chai require(...)
      | should\b                #   should-style (should.equal, should ...)
      | Expect\b                #   Go: Expect() (gomega)
      | XCTAssert               #   XCTest
      | assertEqual | assertTrue | assertFalse | assertThrows
        | assertContains | assertRaises          #   JUnit / google test
      | EXPECT_ | ASSERT_       #   google test macros
      | Verify\b                #   moq / verify
    )
    """,
    re.VERBOSE | re.MULTILINE,
)


def _edit_pairs(tool_input: dict):
    """Yield ``(old_string, new_string)`` text pairs from the edit payload.

    Write has no ``old_string`` (full-file overwrite) — yielded as
    ``(None, content)`` so the caller can still scan the new text for
    *added* skip markers, but cannot detect removed asserts (there is no
    prior text to diff against). Edit yields its single pair; MultiEdit
    yields one per element.
    """
    content = tool_input.get("content")
    if isinstance(content, str):
        yield (None, content)
    new_s = tool_input.get("new_string")
    if isinstance(new_s, str):
        yield (tool_input.get("old_string"), new_s)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                yield (e.get("old_string"), e["new_string"])


def _assert_fingerprint(line: str) -> str | None:
    """A normalised fingerprint for an assertion statement line, or ``None``.

    A line that merely *moved* or was *reformatted* (extra spaces, a trailing
    comment) must NOT count as a removed assertion — that is the false-positive
    that would over-gate legitimate edits. We therefore compare fingerprints,
    not raw text: strip leading/trailing whitespace, drop a trailing ``#``
    comment, and collapse internal whitespace runs to single spaces. Returns
    ``None`` if the line is not an assertion statement (so the caller's set
    diff only ever contains real assertions).
    """
    if not _ASSERT_LINE.match(line):
        return None
    body = line.strip()
    # Drop a trailing ``# ...`` comment (not a ``#`` inside a string — we
    # accept the rare false-equality; the cost is under-counting a removal,
    # i.e. allowing, which is the safe direction).
    if "#" in body:
        body = body.split("#", 1)[0]
    body = re.sub(r"\s+", " ", body).strip()
    return body or None


def _removed_assert_count(old_text, new_text) -> int:
    """Count assertion statements present in ``old`` but absent from ``new``.

    Conservative: only an assertion whose *fingerprint* vanishes entirely from
    the new text counts as removed. A line that merely moved, gained a
    trailing comment, or was reformatted keeps the same fingerprint and does
    NOT count — we diff the set of fingerprints, not the raw lines.
    """
    if not old_text or not new_text:
        return 0
    old_fps = {
        fp
        for ln in old_text.splitlines()
        if (fp := _assert_fingerprint(ln)) is not None
    }
    new_fps = {
        fp
        for ln in new_text.splitlines()
        if (fp := _assert_fingerprint(ln)) is not None
    }
    return sum(1 for fp in old_fps if fp not in new_fps)


def _added_skip_markers(new_text) -> list:
    """Return the skip markers newly present in ``new_text``."""
    if not new_text:
        return []
    return [m for m in SKIP_MARKERS if m in new_text]


def _load_frozen_locators(track_dir):
    """Read the frozen anchor list → ``(anchor_path, set(test_locator))``.

    Returns ``(None, set())`` when there is no list (track never froze one) or
    the JSON is unreadable. ``anchor_path`` is the absolute path to the
    ``feature-list.json`` itself (for protection #1). ``test_locator`` values
    are normalised to a ``(rel_path, fn)`` form where possible so we can match
    both whole-file edits and function-scoped edits.

    Locator forms recognised (from the feature-list contract):
      - "tests/api/test_users.py::test_TC_1_1_returns_paginated_list"
      - "tests/api/test_users.py::test_TC_1_1_*"
      - "tests/api/test_users.py"  (whole-file anchor)
    """
    anchor_path = Path(track_dir) / ".conductor" / ANCHOR_FILENAME
    if not anchor_path.exists():
        return (None, set())
    try:
        import json
        data = json.loads(anchor_path.read_text(encoding="utf-8"))
    except Exception:
        return (anchor_path, set())  # file exists but unreadable → still guard it
    locators = set()
    for feat in data.get("features", []) if isinstance(data, dict) else []:
        loc_raw = feat.get("test_locator") if isinstance(feat, dict) else None
        if not isinstance(loc_raw, str) or not loc_raw.strip():
            continue
        locators.add(loc_raw.strip())
    return (anchor_path, locators)


def _locator_matches_file(rel_target: str, locators: set) -> list:
    """Return the locators whose file component matches the edited file.

    Splits a pytest-style ``path::test_fn`` locator on ``::``; the file part
    is compared to ``rel_target`` (normalised, forward-slash). Returns the
    matching locator strings so the deny reason can name them. Whole-file
    locators (no ``::``) match on the path alone.
    """
    rel = rel_target.replace("\\", "/").lstrip("./")
    hits = []
    for loc in locators:
        file_part = loc.split("::", 1)[0].replace("\\", "/").lstrip("./")
        if not file_part:
            continue
        if rel == file_part or rel.endswith("/" + file_part):
            hits.append(loc)
    return hits


def main():
    input_data = read_hook_input()

    tool = input_data.get("tool_name")
    if tool not in ("Write", "Edit", "MultiEdit"):
        write_hook_output(permission_decision="allow")
        return

    tool_input = input_data.get("tool_input") or {}
    file_path = tool_input.get("file_path", "") or ""
    cwd = input_data.get("cwd") or str(Path.cwd())

    log_file = init_logging("on-anchor-write-guard")
    log_entry(log_file, f"event=anchor_probe tool={tool} path={file_path}")

    # Resolve the active track so we know WHICH feature-list.json governs this
    # edit. Fail-open: no locked task / no track resolvable → allow (the
    # orchestrator between tasks, or a non-conductor session, is out of scope).
    # ``resolve_locked_task`` returns ``(track_dir, phase, task, subtask)`` or
    # ``None`` (see ``lib/locked_task``); unpack defensively.
    try:
        locked = resolve_locked_task(cwd)
    except Exception as exc:
        log_entry(log_file, f"event=allow reason=lock_resolve_error err={exc}")
        write_hook_output(permission_decision="allow")
        return
    if not locked:
        write_hook_output(permission_decision="allow")
        return
    try:
        track_dir, _phase, _task, _subtask = locked
    except (TypeError, ValueError):
        write_hook_output(permission_decision="allow")
        return
    if not track_dir:
        write_hook_output(permission_decision="allow")
        return

    anchor_path, locators = _load_frozen_locators(track_dir)
    if anchor_path is None and not locators:
        # No frozen list on this track → guard is inert.
        write_hook_output(permission_decision="allow")
        return

    rel = resolve_rel_target(file_path, cwd)

    # --- Protection #1: editing the anchor file itself is always denied. ---
    if anchor_path is not None:
        try:
            target_abs = Path(file_path) if file_path.startswith("/") else Path(cwd) / file_path
            same = target_abs.resolve() == anchor_path.resolve()
        except Exception:
            same = False
        if same:
            plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "${CLAUDE_PLUGIN_ROOT}")
            reason = (
                f"Conductor frozen-anchor invariant: you are editing the anchor "
                f"file `{anchor_path.name}` directly. The feature list is FROZEN "
                f"— it is the exogenous reference the coverage gate is anchored "
                f"against, so the executor may never rewrite it (only flip a "
                f"feature's `passes` field, via `write-result`). To add, amend, "
                f"or drop a frozen feature, use the owning command (it records an "
                f"audit entry):\n\n"
                f"  python3 \"{plugin_root}/bin/track-state\" thaw "
                f"--track {track_dir} --feature <F-AC-n> --reason \"<why>\"\n\n"
                f"or, for a fresh freeze from the current spec, "
                f"`track-state freeze`. Do not Edit/Write the JSON by hand."
            )
            log_entry(log_file, f"event=deny kind=anchor_file path={file_path}")
            print(
                f"⚠️  CONDUCTOR ANCHOR-WRITE GUARD: denied {tool} of the frozen "
                f"anchor file `{anchor_path.name}`. Use `track-state thaw`/"
                f"`freeze` to amend it.",
                file=sys.stderr,
            )
            write_hook_output(
                permission_decision="deny", permission_decision_reason=reason
            )
            return

    # --- Protection #2: weakening a frozen test. ---
    if not locators or rel is None:
        write_hook_output(permission_decision="allow")
        return

    matched = _locator_matches_file(rel, locators)
    if not matched:
        write_hook_output(permission_decision="allow")
        return

    # Tally weakening signals across every edit pair in this one tool call.
    removed_asserts = 0
    added_skips = []
    for old_text, new_text in _edit_pairs(tool_input):
        removed_asserts += _removed_assert_count(old_text, new_text)
        added_skips.extend(_added_skip_markers(new_text))

    if removed_asserts == 0 and not added_skips:
        # Editing a frozen test to *strengthen* or *fix* it is allowed — the
        # guard catches regressions in strength, not improvements.
        write_hook_output(permission_decision="allow")
        return

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "${CLAUDE_PLUGIN_ROOT}")
    signals = []
    if removed_asserts:
        signals.append(f"removed {removed_asserts} assertion line(s)")
    if added_skips:
        signals.append(f"added skip marker(s): {', '.join(sorted(set(added_skips)))}")
    loc_list = "; ".join(matched[:3]) + ("; …" if len(matched) > 3 else "")

    reason = (
        f"Conductor frozen-anchor invariant: this edit weakens a test that "
        f"anchors a frozen criterion ({loc_list}). Detected: "
        f"{', '.join(signals)}. A frozen test may be strengthened (more "
        f"assertions) or fixed to pass, but its assertions must not be removed "
        f"and it must not be skipped — those are the load-bearing signal the "
        f"coverage gate is anchored against, and weakening them is exactly the "
        f"Goodhart failure the anchor exists to prevent.\n\n"
        f"If the test genuinely must change, thaw it through the audit path so "
        f"the decision is recorded:\n\n"
        f"  python3 \"{plugin_root}/bin/track-state\" thaw "
        f"--track {track_dir} --locator \"{matched[0]}\" --reason \"<why>\"\n\n"
        f"then re-edit. Otherwise, keep the assertions and remove any skip you "
        f"just added."
    )
    log_entry(
        log_file,
        f"event=deny kind=frozen_weaken path={rel} removed_asserts="
        f"{removed_asserts} added_skips={sorted(set(added_skips))}",
    )
    print(
        f"⚠️  CONDUCTOR ANCHOR-WRITE GUARD: denied {tool} of frozen test "
        f"`{rel}` — weakening detected ({', '.join(signals)}). Thaw via "
        f"`track-state thaw` to amend.",
        file=sys.stderr,
    )
    write_hook_output(
        permission_decision="deny", permission_decision_reason=reason
    )


if __name__ == "__main__":
    main()
