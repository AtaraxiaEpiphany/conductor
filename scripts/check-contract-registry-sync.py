#!/usr/bin/env python3
"""check-contract-registry-sync — drift gate for registry vocab in docs + code.

The plan-format contract and its sibling docs are read verbatim by agents
(``spec-planner`` §4.2 reads ``plan-format-contract.md``; ``spec-reviewer`` reads
its own body; ``core-contract.md`` is the gates reference). Their job is
**grammar and invariants** — NOT a hand-maintained tag/mode/shape/verifier
vocabulary. That vocabulary + semantics live in the resolved registries
(``task-type-profiles.json``, ``verify-mode-profiles.json``,
``workflow-shapes.json``, ``verifier-profiles.json`` — plugin baseline ⊕ project
overlay), rendered by ``track-state registry-doc`` and injected into agents as
the ``[Conductor Registry]`` block.

A hand-maintained vocab enumeration in any watched doc would be a third home for
the vocabulary (alongside the registry and the injected block) and the first to
drift: a project overlay adds a tag/mode/shape/verifier and the doc silently
contradicts it. Two confirmed live drift bugs prove this is real, not
hypothetical — ``core-contract.md`` omitted ``[Migrate]`` from the F2/F3
exemption set (Migrate IS ``tdd_exempt``/``coverage_exempt`` in the registry),
and ``spec-reviewer.md`` enumerated a tag set that omitted ``[Refactor]``. This
script makes "never drift" a CI guarantee, not a hope — the same discipline as
``check-plan-annotations``.

What it flags
-------------
Two detectors, run against every watched file:

1. **Table-row enumeration** (the original detector). A markdown table row (a
   line beginning with ``|``) whose first cell is a known tag/mode/shape/verifier
   literal — the data-duplication shape.

2. **Prose closed-set enumeration** (new). A non-table line that *enumerates* a
   closed vocab — ≥2 distinct resolved-vocab literals (tag/mode/shape/verifier)
   on one line AND a closed-set marker phrase (``closed``, ``only``,
   ``exempted``, ``vocabulary``, ``the set``, ``MODE_VOCAB``, …). This is the
   shape of "Exempted task types ONLY: ``[Docs]``, ``[Config]``, …" and "Tags
   (``[Explore]``/``[Docs]``/…) are TDD exemptions" — the two confirmed drift
   bugs. A grammar example (``- [ ] [Migrate] bump spring-boot``) carries only
   one literal and no marker, so it does not trip (mirrors the original lint's
   prose exemption).

3. **Code-literal assertion** (new). The Tier-1 code sites that branch on
   registry-value names (``verifiers_for``, ``validate_verify_none_closure``,
   ``derive_task_tag``) must read the vocab via an accessor / registry flag, not
   a bare literal set — guarding the Part-3 data-driving against regression.

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
from track_state import verifier_profiles as vfp  # noqa: E402


# The watched doc set: every file that re-states registry vocab in prose must be
# policed here. Adding a doc that quotes tags/modes/shapes/verifiers in a
# closed-set context means adding its path here.
WATCHED = [
    "runtime/contracts/plan-format-contract.md",
    "runtime/core-contract.md",
    "agents/spec-planner.md",
    "agents/spec-reviewer.md",
]

# Two independent trip conditions for a prose closed-set enumeration, both keyed
# on ADJACENCY — a single comma/slash run holding the literals — so scattered
# `e.g.` examples (one literal per clause) never trip:
#
#  (a) BRACKETED-TAG run of ≥3 tags. Tags appear one-at-a-time in a grammar
#      template (``- [ ] [Migrate] bump spring-boot``) and at most one per
#      `e.g.` example clause; a run of 3+ bracketed tags adjacently
#      (``[Explore]/[Docs]/[Config]``) is always a "the tag set is …" claim.
#      This is the signal that catches spec-reviewer.md:84 and core-contract.md
#      :30/:34 (the three confirmed tag drifts) — with OR without a marker.
#
#  (b) BARE-IDENTIFIER run (modes/shapes/verifiers) of ≥3 WITH a strong
#      closed-set marker on the same line. Modes legitimately appear 2-at-a-time
#      in directive examples (``verify: test,start``), so a bare-mode run needs
#      BOTH the ≥3 length AND the completeness marker to be a closed-set claim.
#      This is the signal that catches spec-reviewer.md:104 (the ``MODE_VOCAB``
#      enumeration). The contract's grammar/resolution prose mentions modes
#      freely but never carries a completeness marker on the same line as a
#      3+ mode run, so it is spared.
_CLOSED_SET_MIN_BRACKETED = 3   # bracketed-tag run alone is sufficient
_CLOSED_SET_MIN_BARE = 3        # bare-identifier run needs a marker too

# Strong markers that signal the line is asserting a *complete* set ("these and
# no others"). The drift bug is always a stale *complete* set; an example list is
# not load-bearing and may legitimately be partial. Matched case-insensitively.
#
# These are deliberately NARROW: bare words like "closed"/"exemption"/"vocabulary"
# appear in unrelated subset contexts ("an unclosed none", "an exemption tag",
# "the closed tag set is data-driven"), so they are reliable only in a compound
# phrase that asserts completeness — "ONLY:" (with colon), "exhaustive",
# "complete set", "the set of", or a literal vocab-name token (``MODE_VOCAB``).
_CLOSED_SET_MARKERS = (
    "only:", "only :",            # "Exempted task types ONLY:" — canonical drift
    "exhaustive", "complete set", "the set of",
    "mode_vocab", "tag_vocab", "shapes_vocab", "verifier_vocab",
    "closed vocabulary", "closed mode vocabulary", "closed tag vocabulary",
    "closed six",                 # "outside the closed six" (spec-reviewer)
)


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


def _vocab_literals():
    """The four resolved vocabs as a single {literal: kind} map.

    Tag literals are the bracketed form (``[Explore]``) — how they appear in
    plan.md / docs. Modes/shapes/verifiers are bare (``compile``, ``default``,
    ``ac-tracer``). ``kind`` is the remediation hint for the finding message.
    """
    literals = {}
    for t in tp.TAG_VOCAB():
        literals[f"[{t}]"] = "tag"
    for m in vmp.MODE_VOCAB():
        literals[m] = "mode"
    for s in ws.SHAPES_VOCAB():
        literals[s] = "shape"
    for v in vfp.VERIFIER_VOCAB():
        literals[v] = "verifier"
    return literals


def _prose_literals_on_line(line, literals):
    """The set of distinct vocab literals (bracketed tags, bare modes/shapes/verifiers)
    that appear as tokens on a prose line.

    Tags are matched as the bracketed ``[Tag]`` token (so ``Migrate`` inside a
    word like ``migration`` does not false-positive). Modes/shapes/verifiers are
    matched as whole, case-sensitive words — they are lowercase identifiers in
    the registry, and a case-sensitive whole-word match avoids hitting
    ``compile`` inside ``compiled``/``compiler``.
    """
    found = set()
    # Bracketed tag tokens first (word-boundary on the brackets).
    for m in re.finditer(r"\[([A-Za-z]+)\]", line):
        tok = f"[{m.group(1)}]"
        if tok in literals:
            found.add(tok)
    # Bare identifiers: split on non-word chars, match case-sensitively.
    for tok in re.split(r"\W+", line):
        if tok in literals and not tok.startswith("["):
            found.add(tok)
    return found


def _is_closed_set_line(line):
    """True iff the line carries a strong closed-set marker phrase (case-insensitive)."""
    low = line.lower()
    return any(marker in low for marker in _CLOSED_SET_MARKERS)


def _literals_in_token(tok, bracketed, bare):
    """The set of vocab literals contained in one list-run token."""
    hits = set()
    for b in bracketed:
        if b in tok:  # bracketed tags may be backtick-wrapped
            hits.add(b)
    for m in bare:
        # Whole-word so "compile" doesn't hit "compiled"/"compiler".
        if re.search(rf"\b{re.escape(m)}\b", tok):
            hits.add(m)
    return hits


def _max_per_run(line, literals):
    """The most bracketed-tags AND most bare-identifiers in any one list-run.

    Returns ``(max_bracketed_in_run, max_bare_in_run)``. A "list-run" is a
    maximal sequence of vocab-literal tokens separated ONLY by ``,`` / ``/``
    list punctuation and whitespace — the shape of a real enumeration
    (``[Docs], [Config], [Chore]`` or ``compile / test / start``). Scattered
    `e.g.` examples (one literal per clause, separated by ``;`` and prose) never
    form such a sequence, so they do not trip — this is what keeps the detector
    off the anti-drift instruction itself (spec-planner.md:143) while still
    firing on a real list.

    Implementation: backtick code spans are made ATOMIC first (a span like
    `` `verify: test,start` `` is one token, so the comma inside it does NOT
    split the run and the two modes inside it count as one token, not two).
    Then the line is split on comma/slash/newline, and a left-to-right walk
    counts consecutive literal tokens, breaking the run whenever a non-literal,
    non-separator (prose) token appears. Bracketed tags and bare identifiers
    are counted in separate runs so the two trip conditions apply to the right
    vocabulary kind.
    """
    bare = {l for l in literals if not l.startswith("[")}
    bracketed = {l for l in literals if l.startswith("[")}

    # Make each backtick span atomic: replace its internal commas/slashes with
    # placeholders so the split below treats the whole span as one token. The
    # span still carries its literal(s) for the _literals_in_token check.
    def _mask_span(m):
        return m.group(0).replace(",", "\x00").replace("/", "\x01")
    masked = re.sub(r"`[^`]*`", _mask_span, line)

    tokens = re.split(r"[,/\n]", masked)

    def lit_kind(tok):
        hits = _literals_in_token(tok, bracketed, bare)
        if not hits:
            return None
        if all(h.startswith("[") for h in hits):
            return "bracketed"
        return "bare"

    best_b = best_m = 0
    cur_b = cur_m = 0  # current consecutive run of each kind
    for tok in tokens:
        kind = lit_kind(tok)
        if kind == "bracketed":
            cur_b += 1
            cur_m = 0
        elif kind == "bare":
            cur_m += 1
            cur_b = 0
        else:
            # Non-literal token. It breaks the run UNLESS it is pure
            # separator/whitespace (a ", " or " / " gap), which is the glue
            # between literals in a list.
            stripped = tok.replace("\x00", "").replace("\x01", "").strip().strip(",/").strip()
            if stripped == "":
                pass  # separator-only — run continues
            else:
                cur_b = cur_m = 0  # prose — run breaks
        best_b = max(best_b, cur_b)
        best_m = max(best_m, cur_m)
    return best_b, best_m


def _scan_doc(path, literals):
    """Yield (lineno, message) findings for one watched doc — both detectors."""
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        # Detector 1: table-row enumeration.
        cell = _first_cell(line)
        if cell is not None and cell in literals:
            kind = literals[cell]
            yield (lineno,
                   f"{kind} `{cell}` enumerated as a table row — {kind} vocabulary "
                   f"belongs in the registry (`track-state registry-doc`), not a "
                   f"hand-maintained table in this doc")
            continue  # a table row is not also prose-scanned
        # Detector 2: prose closed-set enumeration. The signal is ADJACENCY —
        # literals listed together in one comma/slash run, not scattered one-per-
        # clause as `e.g.` examples. Two trip conditions (see the constants above):
        #  (a) a bracketed-TAG run of ≥3 (always a "the tag set is …" claim —
        #      grammar templates carry one tag, never 3 adjacently); OR
        #  (b) a bare-IDENTIFIER run of ≥3 (modes/shapes/verifiers) WITH a strong
        #      closed-set marker (modes appear legitimately in `verify: test,start`
        #      examples, so a bare run needs both length and the marker).
        max_bracketed, max_bare = _max_per_run(line, literals)
        is_tag_run = max_bracketed >= _CLOSED_SET_MIN_BRACKETED
        is_marked_mode_run = (max_bare >= _CLOSED_SET_MIN_BARE
                              and _is_closed_set_line(line))
        if is_tag_run or is_marked_mode_run:
            found = _prose_literals_on_line(line, literals)
            listed = ", ".join(sorted(found))
            yield (lineno,
                   f"closed-set enumeration of {len(found)} vocab literals "
                   f"({listed}) — a hand-listed complete set is a drift surface "
                   f"(a registry/overlay change silently contradicts it). Reference "
                   f"the registry instead: 'task types whose profile is "
                   f"tdd_exempt' / 'the injected [Conductor Registry] block'.")


def _scan_code_literals(root):
    """Assert the Tier-1 code sites read vocab via accessors, not bare literals.

    Each site that branches on a registry-value name must contain an accessor or
    registry-flag call in the relevant branch — guarding the Part-3 data-driving
    against a regression that re-introduces a hardcoded literal set. A targeted
    substring check on the function source, not a full AST walk — matches the
    lint's pragmatic style.
    """
    findings = []
    checks = [
        # (file, function-anchor, must-contain-substring, what-it-is)
        ("scripts/track_state/workflow_shapes.py", "def verifiers_for",
         "build_gated",  # is_build_gated() / the build_gated flag
         "verifiers_for build-gate branch must read the registry flag, not "
         "bare 'none'/'compile' literals"),
        ("scripts/track_state/plan_parse.py", "def validate_verify_none_closure",
         "closing_modes",  # vmp.closing_modes() / debt_modes()
         "validate_verify_none_closure must read closing/debt modes from the "
         "registry, not a bare {'compile','test','start'} literal set"),
        ("scripts/track_state/task_profiles.py", "def derive_task_tag",
         "over_tag_risk",  # has_over_tag_risk()
         "derive_task_tag over-tag guard must read the registry flag, not a "
         "bare ('Docs','Config','Chore') literal tuple"),
    ]
    for rel, anchor, needle, what in checks:
        f = root / rel
        if not f.exists():
            findings.append(f"{rel}: file missing — cannot assert {what}")
            continue
        src = f.read_text(encoding="utf-8")
        # Slice from the function anchor to the next top-level def (its body).
        start = src.find(anchor)
        if start == -1:
            findings.append(f"{rel}: `{anchor}` not found — cannot assert {what}")
            continue
        nxt = src.find("\ndef ", start + 1)
        body = src[start:nxt] if nxt != -1 else src[start:]
        if needle not in body:
            findings.append(f"{rel}: `{anchor}` — {what} (expected `{needle}` "
                            f"in the function body)")
    return findings


def main():
    root = get_plugin_root()

    literals = _vocab_literals()

    findings = []
    for rel in WATCHED:
        path = root / rel
        if not path.exists():
            # A watched doc may be absent in a stripped-down checkout; skip
            # silently rather than mask a real finding behind a missing-file error.
            continue
        for lineno, msg in _scan_doc(path, literals):
            findings.append(f"  {rel}:{lineno}: {msg}")

    findings.extend(_scan_code_literals(root))

    if findings:
        sys.exit(
            "HALT: registry-vocab drift detected.\n"
            "A hand-maintained tag/mode/shape/verifier enumeration (table or "
            "prose closed-set) is a third home for the vocabulary (registry + "
            "injected [Conductor Registry] block + this doc/code) and the first "
            "to drift — a project overlay adding a tag/mode/shape/verifier would "
            "silently contradict it.\n"
            "Reference the registry instead: the resolved vocab is rendered by "
            "`track-state registry-doc` (full tables) / `--tag <Name>` / "
            "`--mode <name>` / `--shape <name>`, the [Conductor Registry] block "
            "injected into agents is authoritative at dispatch, and the F2/F3 "
            "exemption sets derive from each tag's `tdd_exempt`/`coverage_exempt` "
            "profile field.\n"
            "Findings:\n" + "\n".join(findings)
        )

    print("OK: watched docs carry no hand-maintained tag/mode/shape/verifier "
          "enumeration (table or prose closed-set), and Tier-1 code sites read "
          "the vocab via registry accessors (vocab is registry-sourced).")


if __name__ == "__main__":
    main()
