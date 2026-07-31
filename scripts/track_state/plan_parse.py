"""Parse and validate plan.md for init-from-plan.

plan.md grammar (see agents/spec-planner.md §4.2):

    # Implementation Plan: <title>                 (ignored)

    ## Phase <N>: <Phase Name>                      (N starts at 1, contiguous)
    - [ ] <desc> <!-- AC-1, TC-1.1 -->               (top-level task)
      - [ ] <desc>                                   (indented = subtask)
    - [ ] [Manual] ...                               (each phase ends with one)

Checkbox marker chars are the values of constants.MARKER_MAP:
    ' ' pending   x completed   ~ in_progress   ! failed
    > skipped     # blocked      - cancelled     d deferred

This module produces the PLAN_STRUCTURE shape that quality._init_core expects,
plus structural diagnostics. Name cleaning mirrors validate._parse_plan_structure
so a structure derived here round-trips cleanly with sync-plan.
"""
import re
from pathlib import Path

from .constants import MARKER_MAP
from .helpers import _clean_trailing_markers

# "## Phase 1", "## Phase 1: Build", "## Phase 1 - Build", "## Phase 1 — Build".
# group(1)=number, group(2)=name (may be empty / None).
_PHASE_HEADING = re.compile(r"^##\s+Phase\s+(\d+)\b\s*[:：\-—]?\s*(.*)$")

# Well-formed checkbox line. group(1)=indent, group(2)=marker char, group(3)=rest.
# Trailing content optional so an empty `- [ ]` is caught as an empty name.
_TASK_LINE = re.compile(r"^(\s*)-\s+\[([ x~!>#\-d])\]\s*(.*)$")

# A bracket with exactly one char that is NOT a valid marker, e.g. "- [X] ...".
# Multi-char brackets ([Manual], [checkpoint:abc1234], [N/A]) never match because
# they lack a ']' immediately after the first char — so they are left untouched.
_BAD_MARKER_LINE = re.compile(r"^(\s*)-\s+\[(.)\]\s*.*$")

# A task/subtask bullet MISSING its checkbox entirely, e.g. "- Subtask: x" or
# "- [Explore] Task: x" (tag present but no [ ] status marker). Without this, the
# fall-through branch silently drops the line and the subtask vanishes from state.
# Mirrors scripts/check-plan-checkboxes.py (the Write/Edit hook) — defense in
# depth for direct edits the hook cannot see.
_MISSING_CHECKBOX_LINE = re.compile(
    r"^(\s*)-\s+(?!\[[ x~!>#\-d]\])(?:\[[A-Za-z]+\]\s*)?(task|subtask)\b",
    re.IGNORECASE,
)

# A non-checkbox dash bullet carrying an HTML comment (``<!-- AC/TC/deps -->``)
# but NO Task:/Subtask: keyword — an author who dropped the keyword yet kept the
# annotation. Prose bullets don't carry AC/TC/deps comments, so a comment is a
# strong "this was meant to be a task" signal; without this catch the line is
# silently dropped from track-state.json (data loss). Keyword-independent safety
# net so softening the Task:/Subtask: convention does not widen the silent-drop
# hole. Mirrors scripts/check-plan-checkboxes.py. The negative lookahead keeps
# well-formed ``- [ ] …`` lines out; the malformed-bracket guard above handles
# ``- [] …``-style botched brackets before this runs.
_MISSING_CHECKBOX_ANNOTATED = re.compile(
    r"^(\s*)-\s+(?!\[[ x~!>#\-d]\])(?:\[[A-Za-z]+\]\s*)?.*<!--"
)

# A dash-bullet whose first token is a bracket group [...] of ANY width. Used to
# catch the bracket-MALFORMED case (below): a writer who intended a task but
# botched the checkbox. group(1)=indent, group(2)=the bracket token incl. brackets.
_BRACKET_TOKEN = re.compile(r"^(\s*)-\s+(\[[^\]]*\])")

# Bracket tokens that are NOT checkboxes but are legitimate first tokens of a
# dash-bullet: dispatch tags ([Manual], [Explore], ...) and the trailing status
# markers ([N/A], [verified], [sha]). These route through _MISSING_CHECKBOX_LINE
# (tag-but-no-checkbox) or are prose — they must NOT be flagged as malformed, so
# the malformed guard below skips them. Dispatch tags mirror the registry at
# templates/workflow/task-type-profiles.json (the single source of truth,
# surfaced via task_profiles.TAG_VOCAB); trailing markers mirror
# _RE_TRAILING_MARKER.
from .task_profiles import TAG_VOCAB as _tag_vocab
_KNOWN_BRACKET_TOKEN = re.compile(
    r"^\[(?:" + "|".join(_tag_vocab()) + r"|N/A|verified|[0-9a-fA-F]{7,})\]$",
    re.IGNORECASE,
)

# An unrecognized tag-shaped bracket token in a task NAME — e.g. ``[Migration]``
# (typo for ``[Migrate]``), ``[Springboot3]`` (an unregistered tag), or
# ``[K8sRollout]``. These are the silent-drift defect: ``extract_tags`` drops
# them and the task silently falls back to default TDD, with wrong executor
# behavior and no error. This matches any bracket whose content has ≥2 chars and
# starts with a letter — so it catches alphanumeric invented tags too. Pure-hex
# SHAs (``[abcdef1]``), ``[N/A]``, and ``[verified]`` are excluded in
# :func:`_find_unknown_tags` (they are legitimate trailing markers, not tags —
# and ``_clean_name`` has already stripped most of them anyway). Registered tags
# are also excluded there by case-insensitive comparison against the vocab.
_UNKNOWN_TAG = re.compile(r"\[([A-Za-z][^\[\]]{1,})\]")

# Trailing checkpoint marker on a phase heading: [checkpoint:abcdef1]
_CHECKPOINT = re.compile(r"\[checkpoint:\s*[0-9a-f]+\]", re.IGNORECASE)

# AC/TC traceability refs live inside ``<!-- AC-1, TC-1.1 -->`` comments on the
# parent task line (plan-format-contract.md rule 6). Captured BEFORE _clean_name
# strips the comment, so ac_integrity can trace ACs → tasks without changing the
# stored task name or the PLAN_STRUCTURE shape.
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_AC_REF = re.compile(r"AC-\d+")
_TC_REF = re.compile(r"TC-\d+\.\d+")

# Inter-task dependency refs live inside a ``<!-- deps: P1.T2, P1.T3 -->``
# comment on a top-level task line (plan-format-contract.md rule 8). Like
# ac_refs/tc_refs these are advisory metadata for a future scheduler and are
# NOT persisted into track-state.json (to_plan_structure drops them, same as
# ac_refs/tc_refs). validate_deps checks dangling refs, self-deps, and cycles.
# P{n}.T{n} is the runtime's own positional coordinate (current_phase_index /
# current_task_index, lint-track-state's "P{pi}.T{ti}" notation) — so a dep
# names the exact unit the orchestrator addresses internally.
_DEPS_COMMENT = re.compile(r"^\s*deps\s*:", re.IGNORECASE)
_DEPS_REF = re.compile(r"P(\d+)\.T(\d+)")

# Per-phase verify directive, inside a ``<!-- verify: compile -->`` comment on
# the ``## Phase N:`` heading line (plan-format-contract.md §"Phase Verify
# Directives"). A phase whose goal is "compiles" (a mid-migration phase where
# the test suite is expected red) declares ``verify: compile``; the final
# integration phase may declare ``verify: test,start``. A phase whose safety net
# is the frozen anchor declares ``verify: anchor`` (or ``test,anchor``). Absent
# = full gate (the default, backward-compatible). Like ac_refs/deps_refs this is
# advisory metadata parsed for validation surface and the phase-checker's direct
# read of plan.md — NOT persisted into track-state.json (to_plan_structure drops
# it).
_VERIFY_COMMENT = re.compile(r"^\s*verify\s*:", re.IGNORECASE)
# Closed mode vocabulary — now sourced from the registry at
# templates/workflow/verify-mode-profiles.json (the single source of truth,
# surfaced via verify_mode_profiles.MODE_VOCAB), exactly as the dispatch-tag
# vocabulary above is sourced from task_profiles.TAG_VOCAB. ``compile`` gates on
# a green build (not the suite); ``test`` gates on the suite (the default);
# ``start`` adds a one-shot app-boot smoke check; ``anchor`` additionally gates
# on the frozen test subset passing (the Goodhart counter-anchor — see
# anchor.py). Comma-separated, order-free.
#
# RESOLVED LIVE, not frozen at import. ``_extract_verify`` and the diagnostics
# below call ``_mode_vocab()`` per use (a one-shot per phase, not a hot loop),
# so a project-overlay mode registered post-import (e.g. a ``verify: lint`` row
# added mid-session) is recognized immediately — mirroring the per-call rebuild
# ``helpers.extract_tags`` does for the tag regex (a frozen snapshot here would
# silently drop the overlay mode, the asymmetry the tag side was already fixed
# to avoid). See memory: extract-tags-per-call-rebuild-is-intentional.
from .verify_mode_profiles import MODE_VOCAB as _mode_vocab

# Cross-phase gate group, inside a ``<!-- gate_group: spring3 -->`` comment on
# the ``## Phase N:`` heading line (plan-format-contract.md §"Phase Gate
# Groups"). Phases that share a group name defer their own checkpoint and gate
# TOGETHER at the terminal (last contiguous) member — the cross-phase "red now,
# fixed in a later phase" case (e.g. a migration where P1 bumps the dep, P2
# does the rename, P3 wires it up). Same species as verify_modes: advisory
# metadata re-parsed at every read, NOT persisted into track-state.json
# (to_plan_structure drops it). Group names are free-form (like deps refs), not
# a closed vocabulary — so this directive has no registry counterpart.
_GATE_GROUP_COMMENT = re.compile(r"^\s*gate_group\s*:", re.IGNORECASE)

_VALID_MARKERS = set(MARKER_MAP.values())


def _clean_name(rest):
    """Strip HTML comments then trailing SHA/[N/A]/[verified] markers.

    Dispatch tags ([Manual], [Explore], ...) are preserved — the dispatch
    router reads them from the stored task name.
    """
    # re.DOTALL: strip multi-line <!-- ... --> comments whole so tag-like or
    # marker text inside them can't leak into the cleaned name.
    rest = re.sub(r"<!--.*?-->", "", rest, flags=re.DOTALL).strip()
    rest = _clean_trailing_markers(rest)
    return rest.strip()


def _find_unknown_tags(name):
    """Return unknown tag-shaped tokens in a cleaned task name (de-duped, in order).

    A token is "tag-shaped" if it is a bracket group of ≥2 letters, e.g.
    ``[Migration]`` or ``[Springboot3]``'s alpha part. It is "unknown" if it is
    not a registered dispatch tag (case-insensitive comparison against the
    registry vocab). Trailing SHAs, ``[N/A]``, and ``[verified]`` are not
    alphabetic-bracket tokens, so they never match. ``_clean_name`` has already
    stripped HTML comments and trailing status markers, so any match here is
    almost certainly an intended-but-unregistered tag — the silent-drift defect
    this catches. Returns the raw bracket text (e.g. ``[Migration]``) so the
    error message reads naturally.
    """
    known = {t.lower() for t in _tag_vocab()}
    # Trailing status markers that are NOT tags: [N/A], [verified], and a bare
    # hex SHA [0-9a-f]{7,}. (_clean_name strips most of these already, but a
    # SHA-looking token could survive mid-name, so guard defensively.)
    sha_re = re.compile(r"^[0-9a-fA-F]{7,}$")
    status_words = {"n/a", "verified"}
    seen, out = set(), []
    for m in _UNKNOWN_TAG.finditer(name):
        inner = m.group(1)
        low = inner.lower()
        if low in known or low in status_words:
            continue
        if sha_re.match(inner):
            continue
        token = m.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _extract_refs(rest):
    """Pull AC-n / TC-n.m IDs from ``<!-- ... -->`` comments on a task line.

    Returns ``(ac_refs, tc_refs)`` as de-duped lists in first-seen order. Only
    refs inside HTML comments count — a stray ``AC-1`` in prose is ignored, so
    the annotation contract (rule 6) is what's measured.
    """
    ac_refs, tc_refs = [], []
    for m in _HTML_COMMENT.finditer(rest):
        body = m.group(1)
        for ref in _AC_REF.findall(body):
            if ref not in ac_refs:
                ac_refs.append(ref)
        for ref in _TC_REF.findall(body):
            if ref not in tc_refs:
                tc_refs.append(ref)
    return ac_refs, tc_refs


def _extract_deps(rest):
    """Pull ``P{n}.T{n}`` dependency refs from a ``<!-- deps: ... -->`` comment.

    Returns ``(deps_refs, has_deps_comment, failures)``:
    - deps_refs: de-duped canonical ``P{n}.T{n}`` strings (zero-padded ints so
      ``P1.T02`` normalizes to ``P1.T2``), first-seen order.
    - has_deps_comment: True iff a ``<!-- deps: ... -->`` comment was present, so
      a comment that yielded zero valid refs (likely a typo) can be flagged.
    - failures: tokens inside a deps comment that did not match ``P{n}.T{n}``
      (e.g. ``P1``, ``T2``, ``P1.T2.S1``), surfaced as parse warnings.

    Only a comment whose body starts with ``deps:`` is treated as a deps
    comment — a stray ``deps`` inside an AC/TC comment cannot trigger this.
    """
    deps_refs, seen = [], set()
    has_deps_comment = False
    failures = []
    for m in _HTML_COMMENT.finditer(rest):
        body = m.group(1)
        if not _DEPS_COMMENT.match(body):
            continue
        has_deps_comment = True
        # Drop the leading "deps:" keyword (and its surrounding whitespace),
        # then tokenize the remainder on commas/whitespace. Anything left is
        # either a valid P{n}.T{n} ref or an unparsed token (typo) — never the
        # keyword itself, which sub() removed.
        payload = _DEPS_COMMENT.sub("", body, count=1)
        for tok in re.split(r"[,\s]+", payload):
            if not tok:
                continue
            dm = _DEPS_REF.fullmatch(tok)
            if dm:
                canon = f"P{int(dm.group(1))}.T{int(dm.group(2))}"
                if canon not in seen:
                    seen.add(canon)
                    deps_refs.append(canon)
            else:
                failures.append(tok)
    return deps_refs, has_deps_comment, failures


def _extract_verify(rest):
    """Pull the per-phase verify modes from a ``<!-- verify: ... -->`` comment.

    Returns ``(verify_modes, has_verify_comment, failures)``:
    - verify_modes: de-duped lowercased modes from the closed vocabulary
      (``compile``/``test``/``start``/``anchor``), first-seen order.
    - has_verify_comment: True iff a ``<!-- verify: ... -->`` comment was
      present, so a comment that yielded no valid mode (likely a typo) can be
      flagged.
    - failures: tokens inside a verify comment that did not match a known mode
      (e.g. ``buil``), surfaced as parse warnings.

    Only a comment whose body starts with ``verify:`` is treated as a verify
    directive — a stray ``verify`` inside an AC/TC comment cannot trigger this.
    """
    # Resolve the live registry vocab per call (not a module-level snapshot) so
    # a project-overlay mode added post-import is recognized — see the note by
    # ``_mode_vocab`` above. One-shot per phase, so this is free.
    modes_vocab = _mode_vocab()
    modes, seen = [], set()
    has_verify_comment = False
    failures = []
    for m in _HTML_COMMENT.finditer(rest):
        body = m.group(1)
        if not _VERIFY_COMMENT.match(body):
            continue
        has_verify_comment = True
        payload = _VERIFY_COMMENT.sub("", body, count=1)
        for tok in re.split(r"[,\s]+", payload):
            if not tok:
                continue
            low = tok.lower()
            if low in modes_vocab and low not in seen:
                seen.add(low)
                modes.append(low)
            elif low not in modes_vocab:
                failures.append(tok)
    return modes, has_verify_comment, failures


def _extract_gate_group(rest):
    """Pull the cross-phase gate group name from a ``<!-- gate_group: ... -->``.

    Returns ``(gate_group, has_comment, failures)``:
    - gate_group: the lowercased group name string (e.g. ``"spring3"``), or
      ``None`` if no directive / the comment body was empty.
    - has_comment: True iff a ``<!-- gate_group: ... -->`` comment was present,
      so an empty body (``<!-- gate_group: -->``) can be flagged.
    - failures: a garbage token (one that didn't parse as a clean name)
      surfaced as a parse warning. Today the only failure case is an empty body
      (the last non-whitespace token wins); a future grammar tightening could
      reject name characters here without changing the call shape.

    Group names are free-form identifiers (like deps refs), NOT a closed
    vocabulary, so this is a string extraction rather than a vocab membership
    check. Only a comment whose body starts with ``gate_group:`` is treated as
    a gate-group directive.
    """
    gate_group = None
    has_comment = False
    failures = []
    for m in _HTML_COMMENT.finditer(rest):
        body = m.group(1)
        if not _GATE_GROUP_COMMENT.match(body):
            continue
        has_comment = True
        payload = _GATE_GROUP_COMMENT.sub("", body, count=1)
        # Collapse whitespace, lowercase — group identity is case-insensitive
        # (``spring3`` == ``Spring3``). Whitespace inside a name is not allowed;
        # the last whitespace-free token wins so ``<!-- gate_group: a b -->``
        # surfaces as a failure rather than silently picking ``b``.
        tokens = [t for t in re.split(r"\s+", payload) if t]
        if not tokens:
            failures.append(payload.strip() or "<empty>")
            continue
        if len(tokens) > 1:
            failures.append(" ".join(tokens))
            continue
        gate_group = tokens[0].lower()
    return gate_group, has_comment, failures


def parse_plan(plan_path):
    """Parse plan.md → {"phases": [...], "errors": [...], "warnings": [...]}.

    phases is an ordered list of:
        {"name": str, "number": int, "line": int,
         "tasks": [{"name": str, "subtasks": [str, ...], "line": int,
                    "ac_refs": [str, ...], "tc_refs": [str, ...]}, ...]}

    Names are cleaned; dispatch tags are preserved. ``ac_refs``/``tc_refs`` carry
    the AC/TC IDs from each parent task's ``<!-- ... -->`` annotation (captured
    before the comment is stripped); subtasks have neither (they inherit).
    ``to_plan_structure`` drops both, so they never reach track-state.json.
    errors block initialization; warnings are advisory.
    """
    errors = []
    warnings = []
    phases = []
    current_phase = None
    current_task = None
    seen_phase_numbers = set()
    expected_next = 1

    text = Path(plan_path).read_text()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()

        pm = _PHASE_HEADING.match(line)
        if pm:
            num = int(pm.group(1))
            raw_name = pm.group(2) or ""
            # Extract the verify + gate_group directives from the RAW heading
            # remainder (before _CHECKPOINT.sub / _clean_name strip the comment)
            # — same capture-before-strip discipline as task ac_refs/tc_refs.
            verify_modes, has_verify, verify_failures = _extract_verify(raw_name)
            gate_group, has_gg, gg_failures = _extract_gate_group(raw_name)
            name = _CHECKPOINT.sub("", raw_name).strip()
            if num in seen_phase_numbers:
                errors.append(f"line {lineno}: duplicate phase number {num}")
            if num != expected_next:
                errors.append(
                    f"line {lineno}: phase {num} out of order "
                    f"(expected Phase {expected_next})")
            seen_phase_numbers.add(num)
            expected_next = num + 1
            current_phase = {
                "name": name, "number": num, "line": lineno, "tasks": [],
                "verify_modes": verify_modes,
                "verify_has_comment": has_verify,
                "verify_failures": verify_failures,
                "gate_group": gate_group,
                "gate_group_has_comment": has_gg,
                "gate_group_failures": gg_failures,
            }
            phases.append(current_phase)
            current_task = None
            if not name:
                warnings.append(f"line {lineno}: Phase {num} heading has no name")
            continue

        tm = _TASK_LINE.match(line)
        if tm:
            indent, rest = tm.group(1), tm.group(3)
            is_subtask = len(indent) > 0
            if current_phase is None:
                errors.append(
                    f"line {lineno}: task/subtask appears before any Phase heading")
                continue
            ac_refs, tc_refs = _extract_refs(rest)
            name = _clean_name(rest)
            if not name:
                errors.append(f"line {lineno}: empty task/subtask name")
                continue
            # Unknown-tag guard (silent-drift fix): a bracket token that looks
            # like an intended dispatch tag but isn't registered (a typo like
            # [Migration], or a brand-new tag nobody added to the registry).
            # extract_tags silently drops these → wrong executor behavior with no
            # error, so this is a hard error naming the unrecognized token. The
            # contract documents the registry as the closed vocab; the fix is to
            # correct the typo or add a row to task-type-profiles.json.
            unknown = _find_unknown_tags(name)
            if unknown:
                kind = "subtask" if is_subtask else "task"
                errors.append(
                    f"line {lineno}: {kind} '{name}' carries unrecognized tag(s) "
                    f"{', '.join(unknown)} — not in the registry "
                    f"(templates/workflow/task-type-profiles.json). Fix the typo "
                    f"or register the tag. Known tags: "
                    f"{', '.join(_tag_vocab())}.")
                continue
            if is_subtask:
                if current_task is None:
                    errors.append(
                        f"line {lineno}: subtask '{name}' has no parent task "
                        f"(Phase {current_phase['number']})")
                    continue
                # Subtasks inherit AC context from their parent (rule 6) and are
                # stored as plain strings, so no ac_refs/tc_refs here.
                current_task["subtasks"].append(name)
            else:
                deps_refs, has_deps_comment, dep_failures = _extract_deps(rest)
                current_task = {
                    "name": name, "subtasks": [], "line": lineno,
                    "ac_refs": ac_refs, "tc_refs": tc_refs,
                    "deps_refs": deps_refs,
                    "deps_has_comment": has_deps_comment,
                    "deps_failures": dep_failures,
                }
                current_phase["tasks"].append(current_task)
            continue

        # Not a valid task line — but is it a malformed checkbox we should flag?
        bad = _BAD_MARKER_LINE.match(line)
        if bad:
            indent, marker = bad.group(1), bad.group(2)
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: invalid {kind} marker '[{marker}]' ({where}) "
                f"— valid: [ ] [x] [~] [!] [>] [#] [-] [d]")
            continue
        # A bracket that IS present but malformed — empty ("[]", the modal LLM
        # typo for "- [ ]"), whitespace-only ("[  ]"), or wrong-width ("[xy]").
        # Width-1 cases were handled above (_TASK_LINE valid / _BAD_MARKER_LINE
        # invalid); known tags ([Manual], [N/A], ...) skip to _MISSING_CHECKBOX
        # below for the more accurate "missing checkbox" message. Without this
        # guard these lines fall through to the ignored branch and vanish from
        # track-state.json — the same silent-data-loss class the other guards
        # prevent, for the bracket-malformed case they all miss (0 / 2+ chars
        # sit in the gap between _TASK_LINE's one-valid-char and
        # _BAD_MARKER_LINE's exactly-one-char).
        malformed = _BRACKET_TOKEN.match(line)
        if malformed and not _KNOWN_BRACKET_TOKEN.match(malformed.group(2)):
            indent, bracket = malformed.group(1), malformed.group(2)
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: malformed {kind} checkbox '{bracket}' ({where}) "
                f"— use exactly one marker char: [ ] [x] [~] [!] [>] [#] [-] [d]")
            continue
        missing = _MISSING_CHECKBOX_LINE.match(line)
        if missing:
            indent, kw = missing.group(1), missing.group(2).lower()
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: {kind} '{kw}' line is missing its '[ ]' checkbox "
                f"({where}) — write '- [ ] {kw.capitalize()}: ...' so it is not "
                f"silently dropped from track-state.json")
            continue
        annotated = _MISSING_CHECKBOX_ANNOTATED.match(line)
        if annotated:
            indent = annotated.group(1)
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: {kind} line carries an annotation but is missing "
                f"its '[ ]' checkbox ({where}) — write '- [ ] ...' so it is not "
                f"silently dropped from track-state.json")
            continue
        # Everything else (title, prose, blank lines, non-checkbox bullets) ignored.

    if not phases:
        errors.append("plan.md has no '## Phase N' headings")

    # Per-phase structural checks
    for ph in phases:
        label = f"Phase {ph['number']}" + (f" '{ph['name']}'" if ph["name"] else "")
        if not ph["tasks"]:
            errors.append(f"{label}: has no tasks")
            continue
        if "[Manual]" not in ph["tasks"][-1]["name"]:
            warnings.append(
                f"{label}: last task is not a [Manual] verification task "
                f"(expected at the end of every phase)")
        for t in ph["tasks"]:
            if len(t["subtasks"]) == 1:
                warnings.append(
                    f"{label} task '{t['name']}': has 1 subtask "
                    f"(convention is 0 or ≥2)")

    # Dependency-graph validation (plan-format-contract.md rule 8). Advisory in
    # this substrate — deps are inert metadata for a future scheduler, so issues
    # surface as warnings and never block init (a cycle cannot break serial
    # execution under F1). A scheduler wired in later may escalate these to
    # hard errors at its own enforcement layer.
    for issue in validate_deps({"phases": phases}):
        warnings.append(
            f"{issue['at']}: dependency annotation issue ({issue['kind']}) "
            f"— {issue['detail']}")

    # Per-phase verify-directive validation (plan-format-contract.md §"Phase
    # Verify Directives"). Advisory — the directive is read directly from
    # plan.md by phase-checker, so an unknown mode warns at init but does not
    # block (same posture as deps). A typo'd mode would otherwise be silently
    # ignored at checkpoint, leaving the phase on the (safe) full gate — so the
    # warning is the operator's only signal their directive didn't take.
    modes_vocab = _mode_vocab()  # live (overlay-aware) for the diagnostic strings
    for ph in phases:
        label = f"Phase {ph['number']}" + (f" '{ph['name']}'" if ph["name"] else "")
        if ph.get("verify_has_comment") and not ph.get("verify_modes"):
            warnings.append(
                f"{label}: <!-- verify: --> comment has no valid mode "
                f"(expected one or more of: {', '.join(modes_vocab)})")
        for tok in ph.get("verify_failures", []) or []:
            warnings.append(
                f"{label}: unrecognized verify mode '{tok}' "
                f"(expected one or more of: {', '.join(modes_vocab)})")

    # Cross-phase gate-group validation (plan-format-contract.md §"Phase Gate
    # Groups"). Advisory — the directive is read directly from plan.md by the
    # checkpoint gate (helpers._phase_needs_checkpoint / dispatch review), so a
    # malformed group warns at init but does not block (same posture as
    # verify_modes/deps). A bad group would otherwise silently no-op the
    # deferral, leaving member phases on their normal per-phase gate (the safe
    # fallback) — so the warning is the operator's only signal their group
    # declaration didn't take the deferral effect.
    for issue in validate_gate_groups({"phases": phases}):
        warnings.append(
            f"{issue['at']}: gate-group annotation issue ({issue['kind']}) "
            f"— {issue['detail']}")

    # verify: none closure validation (plan-format-contract.md §"Phase Verify
    # Directives"). Advisory — a debt-carrying ``none`` phase must be closed by a
    # later compile/test/start phase; otherwise the staged debt is never
    # exercised at any gate. Same posture as gate-groups/verify_modes: warns at
    # init, never blocks. Directive-only — it cannot verify the closing phase
    # fixes the *same* debt (operator responsibility).
    for issue in validate_verify_none_closure({"phases": phases}):
        warnings.append(
            f"{issue['at']}: verify: none closure issue ({issue['kind']}) "
            f"— {issue['detail']}")

    return {"phases": phases, "errors": errors, "warnings": warnings}


def to_plan_structure(parsed):
    """Convert parse_plan() output → PLAN_STRUCTURE dict for _init_core.

    Drops phase 'number'/'line' and task 'line' metadata. Tasks with subtasks
    carry a 'subtasks' list; tasks without omit the key — matching the contract
    that quality._init_core consumes.
    """
    out_phases = []
    for ph in parsed["phases"]:
        tasks = []
        for t in ph["tasks"]:
            entry = {"name": t["name"]}
            if t["subtasks"]:
                entry["subtasks"] = t["subtasks"]
            tasks.append(entry)
        out_phases.append({"name": ph["name"], "tasks": tasks})
    return {"phases": out_phases}


def collect_ac_refs(parsed):
    """Aggregate every AC-n referenced by a task's ``<!-- AC-n -->`` annotation.

    Returns a de-duped list (first-seen order) across all phases/tasks. Used by
    ac_integrity to compute traceability (spec ACs ∩ plan ACs) and to flag
    dangling refs (plan ACs not present in spec.md).
    """
    refs, seen = [], set()
    for ph in parsed.get("phases", []):
        for t in ph.get("tasks", []):
            for ref in t.get("ac_refs", []):
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
    return refs


def _dep_coord(ref):
    """``"P1.T2"`` → ``(1, 2)``; ``None`` if ref isn't a clean P{n}.T{n}."""
    m = _DEPS_REF.fullmatch(ref)
    return (int(m.group(1)), int(m.group(2))) if m else None


def validate_deps(parsed):
    """Validate ``<!-- deps: ... -->`` references across the whole plan.

    Returns a list of advisory issue dicts. The substrate is inert — nothing
    executes on deps yet, so these surface as parse warnings, not blockers (a
    future scheduler may treat them as hard errors at its own layer).

    Issue kinds:
    - ``empty``:     a ``<!-- deps: -->`` comment yielded no valid P{n}.T{n} ref.
    - ``unparsed``:  a token inside a deps comment did not match P{n}.T{n}.
    - ``dangling``:  a dep target does not exist (no such phase/task).
    - ``self``:      a task depends on itself.
    - ``cycle``:     a dependency cycle (A→B→A), including transitive.

    Each issue: ``{"kind": str, "at": "P{p}.T{t}", "detail": str}``.
    """
    issues = []
    phases = parsed.get("phases", [])

    # Valid top-level task coordinates (deps target top-level tasks only;
    # subtasks are sequentially decomposed and never parallel candidates).
    valid = set()
    for pi, ph in enumerate(phases, 1):
        for ti, _t in enumerate(ph.get("tasks", []), 1):
            valid.add((pi, ti))

    edges = {}  # src (pi,ti) -> [tgt (pi,ti), ...]
    for pi, ph in enumerate(phases, 1):
        for ti, t in enumerate(ph.get("tasks", []), 1):
            at = f"P{pi}.T{ti}"
            src = (pi, ti)
            refs = t.get("deps_refs", []) or []
            if t.get("deps_has_comment") and not refs:
                issues.append({"kind": "empty", "at": at,
                               "detail": "<!-- deps: --> comment has no valid "
                                         "P{n}.T{n} ref"})
            for tok in t.get("deps_failures", []) or []:
                issues.append({"kind": "unparsed", "at": at,
                               "detail": f"unparsed deps token '{tok}' "
                                         f"(expected P{{n}}.T{{n}})"})
            for r in refs:
                tgt = _dep_coord(r)
                if tgt is None or tgt not in valid:
                    issues.append({"kind": "dangling", "at": at,
                                   "detail": f"deps target {r} does not exist"})
                    continue
                if tgt == src:
                    issues.append({"kind": "self", "at": at,
                                   "detail": f"task depends on itself ({r})"})
                    continue
                edges.setdefault(src, []).append(tgt)

    issues.extend(_detect_dep_cycles(edges))
    return issues


def _detect_dep_cycles(edges):
    """Return one ``cycle`` issue per back-edge found via DFS 3-coloring.

    Recursion depth is bounded by plan depth (tasks per phase × phases), which
    is small for real plans; an explicit stack is not warranted here.
    """
    issues = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {}

    def fmt(node):
        return f"P{node[0]}.T{node[1]}"

    def dfs(u, stack):
        color[u] = GRAY
        for v in edges.get(u, []):
            state = color.get(v, WHITE)
            if state == GRAY:
                # v is an ancestor on the current path → cycle. Reconstruct it.
                try:
                    start = stack.index(v)
                    cyc = stack[start:] + [v]
                except ValueError:  # defensive; a GRAY node is on the stack
                    cyc = [v, u, v]
                issues.append({
                    "kind": "cycle", "at": fmt(u),
                    "detail": "dependency cycle: " + " → ".join(fmt(n) for n in cyc),
                })
            elif state == WHITE:
                dfs(v, stack + [v])
        color[u] = BLACK

    for node in list(edges):
        if color.get(node, WHITE) == WHITE:
            dfs(node, [node])
    return issues


def validate_gate_groups(parsed):
    """Validate ``<!-- gate_group: ... -->`` declarations across the plan.

    Returns a list of advisory issue dicts mirroring validate_deps' shape. Gate
    groups are cross-phase declarations (plan-format-contract.md §"Phase Gate
    Groups") — the terminal (last contiguous) member gates the group's
    accumulated diff and on PASS stamps every member; non-terminal members
    defer. So the validation rules are structural:

    Issue kinds:
    - ``single_member``: a group with only 1 member — pointless (a group needs
      ≥2 phases to express "red now, fixed later"); the lone phase gates itself
      already.
    - ``non_contiguous``: a group's member phases are not a contiguous run of
      phase numbers — the terminal is defined as ``members[-1]`` in declaration
      order, but a gap means an unrelated phase sits *inside* the group's span
      and would be skipped by the terminal-gate accumulated-diff range.
    - ``empty``:     a ``<!-- gate_group: -->`` comment with no name.
    - ``unparsed``:  a garbage token inside the comment (e.g. whitespace in the
      name).

    Each issue: ``{"kind": str, "at": "Phase {n}", "detail": str}``. Advisory —
    the directive is read directly from plan.md at checkpoint time, so a bad
    group warns at init but does not block (same posture as verify_modes/deps).
    A malformed group would otherwise silently no-op the deferral, leaving the
    member phases on their normal per-phase gate (the safe fallback).
    """
    issues = []
    phases = parsed.get("phases", [])

    # Map group name -> [phase numbers] in declaration order.
    groups = {}
    for ph in phases:
        name = ph.get("gate_group")
        if name:
            groups.setdefault(name, []).append(ph["number"])

    for gname, members in groups.items():
        # Per-phase failures (empty / unparsed) are surfaced separately below;
        # this loop only validates whole-group structure.
        if len(members) < 2:
            issues.append({
                "kind": "single_member", "at": f"Phase {members[0]}",
                "detail": f"gate_group '{gname}' has only 1 member "
                          f"(needs ≥2 to defer — a lone phase gates itself)"})
            continue
        # Contiguity: members must be a run of consecutive phase numbers in
        # declaration order. A gap is a non-contiguous group (an unrelated phase
        # sits inside the terminal's accumulated-diff range).
        nums = sorted(members)
        contiguous = all(b == a + 1 for a, b in zip(nums, nums[1:]))
        if not contiguous:
            issues.append({
                "kind": "non_contiguous", "at": f"Phase {members[-1]}",
                "detail": f"gate_group '{gname}' members {sorted(members)} "
                          f"are not contiguous (terminal gates the run's "
                          f"accumulated diff — an intervening phase would be "
                          f"skipped)"})

    # Per-phase comment-body failures.
    for ph in phases:
        at = f"Phase {ph['number']}"
        if ph.get("gate_group_has_comment") and not ph.get("gate_group"):
            for tok in ph.get("gate_group_failures", []) or ["<empty>"]:
                issues.append({
                    "kind": "empty", "at": at,
                    "detail": f"<!-- gate_group: --> comment body did not parse "
                              f"('{tok}') — expected a single identifier"})
                break  # one warning per phase is enough
        for tok in ph.get("gate_group_failures", []) or []:
            if ph.get("gate_group"):
                # A parsed name coexists with a failure token only when the body
                # had multiple tokens (whitespace in the name) — flag those.
                issues.append({
                    "kind": "unparsed", "at": at,
                    "detail": f"unparsed gate_group token '{tok}' "
                              f"(expected a single identifier)"})
    return issues


def validate_verify_none_closure(parsed):
    """Validate that every ``<!-- verify: none -->`` phase is closed by a later
    gating phase (plan-format-contract.md §"Phase Verify Directives").

    A ``none`` phase is debt-carrying — it intentionally skips the build/test
    gate because the work it stages will not compile or pass until a *later*
    phase finishes the migration. The contract therefore requires a subsequent
    phase whose gate actually exercises that debt (a mode in {compile, test,
    start}) to close it. This validator surfaces the two failure shapes:

    Issue kinds:
    - ``none_unclosed_terminal``: a ``none`` phase is the plan's last phase — no
      later phase can close the debt.
    - ``none_unclosed_run``: a ``none`` phase is followed only by other ``none``
      phases until the end of the plan — the run never re-enables the gate.

    Each issue: ``{"kind": str, "at": "Phase {n}", "detail": str}``. Advisory
    and directive-only — it cannot verify the closing phase actually fixes the
    *same* debt (operator responsibility). Same posture as validate_gate_groups
    / validate_deps: warns at init, never blocks.
    """
    issues = []
    phases = parsed.get("phases", [])
    CLOSING_MODES = {"compile", "test", "start"}

    for i, ph in enumerate(phases):
        if ph.get("verify_modes") != ["none"]:
            continue
        # Does any strictly-later phase carry a closing gate?
        later_modes = [set(p.get("verify_modes") or []) for p in phases[i + 1:]]
        if not any(CLOSING_MODES & modes for modes in later_modes):
            kind = ("none_unclosed_terminal" if not later_modes
                    else "none_unclosed_run")
            hint = ("it is the last phase"
                    if kind == "none_unclosed_terminal"
                    else "every later phase is also verify: none")
            issues.append({
                "kind": kind, "at": f"Phase {ph['number']}",
                "detail": f"verify: none phase is never closed by a later "
                          f"compile/test/start phase ({hint}) — the debt it "
                          f"stages would never be exercised at a gate"})
    return issues


def collect_deps(parsed):
    """Aggregate every declared dependency as ``(src, tgt)`` coordinate pairs.

    De-duped, first-seen order. A future parallel scheduler consumes this to
    build the ready-set (tasks whose deps are all terminal) — the same role
    ``collect_ac_refs`` plays for traceability. Pairs reference top-level tasks
    only; dangling targets are passed through unchanged (validate_deps flags
    them separately).
    """
    edges, seen = [], set()
    for pi, ph in enumerate(parsed.get("phases", []), 1):
        for ti, t in enumerate(ph.get("tasks", []), 1):
            src = (pi, ti)
            for r in t.get("deps_refs", []) or []:
                tgt = _dep_coord(r)
                if tgt is None:
                    continue
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(key)
    return edges
