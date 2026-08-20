#!/usr/bin/env python3
"""check-contract-registry-sync — drift gate for registry vocab in docs + code.

The plan-format contract and its sibling docs are read verbatim by agents
(``spec-planner`` §4.2 reads ``plan-format-contract.md``; ``spec-reviewer`` reads
its own body; ``core-contract.md`` is the gates reference). Their job is
**grammar and invariants** — NOT a hand-maintained tag/shape vocabulary. That
vocabulary + semantics live in the resolved registries
(``task-type-profiles.json``, ``workflow-shapes.json`` — plugin baseline ⊕
project overlay), rendered by ``track-state registry-doc`` and injected into
agents as the ``[Conductor Registry]`` block.

A hand-maintained vocab enumeration in any watched doc would be a third home for
the vocabulary (alongside the registry and the injected block) and the first to
drift: a project overlay adds a tag/shape and the doc silently contradicts it.
One confirmed live drift bug proves this is real, not hypothetical —
``spec-reviewer.md`` enumerated a tag set that omitted ``[Refactor]``. This
script makes "never drift" a CI guarantee, not a hope — the same discipline as
``check-plan-annotations``.

What it flags
-------------
Three doc/code detectors + two wiring assertions.

**Doc detectors** (per watched file, line by line):

1. **Table-row enumeration** (the original detector). A markdown table row (a
   line beginning with ``|``) whose first cell is a known tag/shape literal —
   the data-duplication shape.

2. **Prose closed-set enumeration.** A non-table line that *enumerates* a closed
   vocab. Two adjacency-keyed trip conditions (literals listed together in one
   comma/slash run, not scattered one-per-``e.g.``-clause):

   - a **bracketed-tag** run of ≥3 (``[Explore]/[Docs]/[Config]``) — always a
     "the tag set is …" claim, no marker needed; OR
   - a **bare-identifier** run of ≥3 shapes WITH a strong closed-set marker
     (``only:``, ``exhaustive``, ``SHAPES_VOCAB``, …).

3. **Code-literal assertion.** The Tier-1 code sites that branch on
   registry-value names (``derive_task_tag``) must read the vocab via an
   accessor / registry flag, not a bare literal set — guarding the
   data-driving against regression.

**Wiring assertions** (close the injection seam this campaign opened):

4. **Defer-implies-injected.** A watched *agent* doc whose body references the
   ``[Conductor Registry]`` block / ``TAG_VOCAB`` must have its filename-stem
   in ``on-subagent-start._REGISTRY_AGENTS`` — else the prose defers to a block
   the agent never receives. This is the assertion that would have caught the
   original half-wired migration (reviewer prose pointed at the block before
   the reviewers were injected) automatically.

5. **Flag-coverage.** A watched agent that audits by a registry flag name
   (``over_tag_risk``, …) is guaranteed the block surfaces the flag's token —
   ``reviewer_block_flags`` is the explicit ``{name: token}`` map of what the
   renderers emit. Closes the loop prose → flag → block-data.

Exit 0 + OK line on success; exit 1 + remediation message on any failure.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from track_state import task_profiles as tp  # noqa: E402
from track_state import workflow_shapes as ws  # noqa: E402


# The watched doc set: every file that re-states registry vocab in prose must be
# policed here. Adding a doc that quotes tags/shapes in a closed-set context
# means adding its path here.
WATCHED = [
    "runtime/contracts/plan-format-contract.md",
    "runtime/core-contract.md",
    "agents/spec-planner.md",
    "agents/spec-reviewer.md",
    "agents/refuter.md",
    "agents/phase-checker.md",
    "agents/task-executor.md",
    "skills/implement/SKILL.md",
    "skills/parallel/SKILL.md",
    "templates/task-workflow.md",
    # The workflow-docfile library (templates/workflow/steps/*.md) is watched
    # DELIBERATELY: same doctrine-template class as templates/task-workflow.md
    # above — agent-read bodies that name tags (each docfile owns ONE tag's
    # workflow prose) and could accrete a stale closed-set enumeration as the
    # library grows. Project-side conductor/workflow/steps/ docfiles are NOT
    # watched: they are project content the plugin does not ship.
    *sorted(str(p.relative_to(Path(__file__).resolve().parent.parent))
            for p in (Path(__file__).resolve().parent.parent
                      / "templates" / "workflow" / "steps").glob("*.md")),
    "skills/new-track/SKILL.md",
    "skills/setup/SKILL.md",
    "skills/review/SKILL.md",
    "skills/status/SKILL.md",
]

# Two independent trip conditions for a prose closed-set enumeration, both keyed
# on ADJACENCY — a single comma/slash run holding the literals — so scattered
# `e.g.` examples (one literal per clause) never trip:
#
#  (a) BRACKETED-TAG run of ≥3 tags. Tags appear one-at-a-time in a grammar
#      template (``- [ ] [Refactor] extract the module``) and at most one per
#      `e.g.` example clause; a run of 3+ bracketed tags adjacently
#      (``[Explore]/[Docs]/[Config]``) is always a "the tag set is …" claim.
#      This is the signal that catches spec-reviewer.md and core-contract.md
#      (the confirmed tag drifts) — with OR without a marker.
#
#  (b) BARE-IDENTIFIER run (shapes) of ≥3 WITH a strong closed-set marker on the
#      same line. The contract's grammar/resolution prose mentions shapes freely
#      but never carries a completeness marker on the same line as a 3+ shape
#      run, so it is spared.
_CLOSED_SET_MIN_BRACKETED = 3   # bracketed-tag run alone is sufficient
_CLOSED_SET_MIN_BARE = 3        # bare-identifier run needs a marker too

# Strong markers that signal the line is asserting a *complete* set ("these and
# no others"). The drift bug is always a stale *complete* set; an example list is
# not load-bearing and may legitimately be partial. Matched case-insensitively.
#
# These are deliberately NARROW: bare words like "closed"/"exemption"/"vocabulary"
# appear in unrelated subset contexts ("an exemption tag", "the closed tag set is
# data-driven"), so they are reliable only in a compound phrase that asserts
# completeness — "ONLY:" (with colon), "exhaustive", "complete set", "the set
# of", or a literal vocab-name token (``TAG_VOCAB``).
_CLOSED_SET_MARKERS = (
    "only:", "only :",            # "Exempted task types ONLY:" — canonical drift
    "exhaustive", "complete set", "the set of",
    "tag_vocab", "shapes_vocab",
    "closed vocabulary", "closed tag vocabulary",
)


def _first_cell(row):
    """The trimmed first cell of a markdown table row, or None if not a table row.

    A table row is a line beginning with ``|``; the first cell is the text up to
    the next ``|``. Surrounding markdown formatting (backticks, bold) is stripped
    so ``| `[Explore]` |`` and ``| **[Refactor]** |`` compare against the bare
    literal ``[Explore]`` / ``[Refactor]``. Separator rows (``|---|---|``) and
    the header rule are skipped — a separator's first cell is empty or dashes,
    never a tag/shape literal.
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
    """The two resolved vocabs as a single {literal: kind} map.

    Tag literals are the bracketed form (``[Explore]``) — how they appear in
    plan.md / docs. Shapes are bare (``default``, ``research-first``). ``kind``
    is the remediation hint for the finding message.
    """
    literals = {}
    for t in tp.TAG_VOCAB():
        literals[f"[{t}]"] = "tag"
    for s in ws.SHAPES_VOCAB():
        literals[s] = "shape"
    return literals


def _prose_literals_on_line(line, literals):
    """The set of distinct vocab literals (bracketed tags, bare shapes) that
    appear as tokens on a prose line.

    Tags are matched as the bracketed ``[Tag]`` token (so ``Refactor`` inside a
    word like ``refactoring`` does not false-positive). Shapes are matched as
    whole, case-sensitive words — they are lowercase/hyphen identifiers in the
    registry, and a case-sensitive whole-word match avoids hitting ``default``
    inside ``defaults``.
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
        # Whole-word so "default" doesn't hit "defaults".
        if re.search(rf"\b{re.escape(m)}\b", tok):
            hits.add(m)
    return hits


def _max_per_run(line, literals):
    """The most bracketed-tags AND most bare-identifiers in any one list-run.

    Returns ``(max_bracketed_in_run, max_bare_in_run)``. A "list-run" is a
    maximal sequence of vocab-literal tokens separated ONLY by ``,`` / ``/``
    list punctuation and whitespace — the shape of a real enumeration
    (``[Docs], [Config], [Chore]``). Scattered `e.g.` examples (one literal per
    clause, separated by ``;`` and prose) never form such a sequence, so they do
    not trip — this is what keeps the detector off the anti-drift instruction
    itself while still firing on a real list.

    Implementation: backtick code spans are made ATOMIC first (a span like
    `` `default, research-first` `` is one token, so the comma inside it does NOT
    split the run and the two shapes inside it count as one token, not two).
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
        #  (b) a bare-IDENTIFIER run of ≥3 (shapes) WITH a strong closed-set
        #      marker.
        max_bracketed, max_bare = _max_per_run(line, literals)
        is_tag_run = max_bracketed >= _CLOSED_SET_MIN_BRACKETED
        is_marked_bare_run = (max_bare >= _CLOSED_SET_MIN_BARE
                              and _is_closed_set_line(line))
        if is_tag_run or is_marked_bare_run:
            found = _prose_literals_on_line(line, literals)
            listed = ", ".join(sorted(found))
            yield (lineno,
                   f"closed-set enumeration of {len(found)} vocab literals "
                   f"({listed}) — a hand-listed complete set is a drift surface "
                   f"(a registry/overlay change silently contradicts it). Reference "
                   f"the registry instead: 'task types whose profile is "
                   f"tdd_exempt' / 'the injected [Conductor Registry] block'.")
            continue


def _scan_code_literals(root):
    """Assert the Tier-1 code sites read vocab via accessors, not bare literals.

    Each site that branches on a registry-value name must contain an accessor or
    registry-flag call in the relevant branch — guarding the data-driving
    against a regression that re-introduces a hardcoded literal set. A targeted
    substring check on the function source, not a full AST walk — matches the
    lint's pragmatic style.
    """
    findings = []
    checks = [
        # (file, function-anchor, must-contain-substring, what-it-is)
        ("scripts/track_state/task_profiles.py", "def derive_task_tag",
         "over_tag_risk",  # has_over_tag_risk()
         "derive_task_tag over-tag guard must read the registry flag, not a "
         "bare ('Docs','Config','Chore') literal tuple"),
        ("scripts/track_state/plan_parse.py", "def parse_plan",
         "route_for",  # route_for(extract_tags(name)) != "manual"
         "phase-end manual-task validator must key off route_for (the registry "
         "route), not a '[Manual]' substring literal — else a project overlay "
         "that renames/adds a manual-route tag silently fails the check"),
        ("scripts/track_state/result.py", "def _evaluate_gates",
         "is_coverage_exempt",  # built from task_profiles.is_coverage_exempt(...)
         "_evaluate_gates coverage-failure remediation must build the exempt "
         "tag list from the registry (task_profiles.is_coverage_exempt), not a "
         "hardcoded '[Docs]/[Config]/[Chore]/[Manual]' literal enumeration"),
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


def _load_hook():
    """Load ``on-subagent-start.py`` (hyphenated → importlib) for its registry set.

    The injection allowlist ``_REGISTRY_AGENTS`` and the flag declaration
    ``reviewer_block_flags`` live in the hook script; this lint asserts against
    both so prose that defers to the block is guaranteed the block arrives, and
    prose that names a flag is guaranteed the block surfaces it.
    """
    import importlib.util
    p = Path(__file__).parent / "on-subagent-start.py"
    spec = importlib.util.spec_from_file_location("on_subagent_start", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check_defer_implies_injected(root, hook):
    """A watched agent that defers to the block must actually receive it.

    The half-wired migration this whole campaign closes: ``spec-reviewer`` /
    ``refuter`` prose said "consult your injected ``[Conductor Registry]`` block"
    while neither was in ``_REGISTRY_AGENTS``, so the block never arrived and the
    prose pointed at data the agent could not see. For each watched AGENT doc
    (contracts document the block; they do not receive it) whose body references
    the block / ``TAG_VOCAB``, assert its filename-stem name is in
    ``_REGISTRY_AGENTS``. This is the assertion that would have caught the
    original bug automatically.
    """
    findings = []
    injected = hook._REGISTRY_AGENTS
    defer_re = re.compile(r"\[Conductor Registry\]|TAG_VOCAB")
    for rel in WATCHED:
        if not rel.startswith("agents/"):
            continue  # contracts document the block; agents receive it
        path = root / rel
        if not path.exists():
            continue
        agent_name = Path(rel).stem
        text = path.read_text(encoding="utf-8")
        if defer_re.search(text) and agent_name not in injected:
            findings.append(
                f"  {rel}: defers to the [Conductor Registry] block / TAG_VOCAB "
                f"but `{agent_name}` is not in "
                f"on-subagent-start._REGISTRY_AGENTS — the block is never injected, "
                f"so the prose points at data that never arrives. Add the agent to "
                f"_REGISTRY_AGENTS and a _registry_for_<agent>() builder.")
    return findings


def _check_flag_coverage(root, hook):
    """Every registry flag a watched agent names must be surfaced by the block.

    Closes the loop prose -> flag -> block-data. ``reviewer_block_flags`` is the
    explicit ``{flag-name: emitted-token}`` map of what the renderers surface; if
    a watched agent's prose audits by one of those names, the rendered reviewer
    block must emit its token — else the prose defers to data the agent cannot
    see. Catches a renderer that stops emitting a flag the prose still names. The
    token comes from the map (not ``name.replace('_','-')``) because one flag
    shortens: ``over_tag_risk`` -> ``over-tag``. The reverse direction — every
    declared token IS emitted — is a unit test's job.
    """
    findings = []
    block = hook._registry_for_reviewer()
    flag_map = hook.reviewer_block_flags()
    flag_re = re.compile(
        r"`?(" + "|".join(re.escape(f) for f in sorted(flag_map, key=len, reverse=True)) + r")`?")
    seen = set()
    for rel in WATCHED:
        if not rel.startswith("agents/"):
            continue
        path = root / rel
        if not path.exists():
            continue
        for m in flag_re.finditer(path.read_text(encoding="utf-8")):
            name = m.group(1)
            key = (rel, name)
            if key in seen:
                continue
            seen.add(key)
            token = flag_map[name]
            if token not in block:
                findings.append(
                    f"  {rel}: audits by flag `{name}` but the [Conductor "
                    f"Registry] block does not emit `{token}` — surface it in "
                    f"_tag_summary_rows (and add it to reviewer_block_flags) or "
                    f"drop the reference.")
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

    hook = _load_hook()
    findings.extend(_check_defer_implies_injected(root, hook))
    findings.extend(_check_flag_coverage(root, hook))

    if findings:
        sys.exit(
            "HALT: registry-vocab drift detected.\n"
            "A hand-maintained tag/shape enumeration (table or prose closed-set) "
            "is a third home for the vocabulary (registry + injected [Conductor "
            "Registry] block + this doc/code) and the first to drift — a project "
            "overlay adding a tag/shape would silently contradict it.\n"
            "Reference the registry instead: the resolved vocab is rendered by "
            "`track-state registry-doc` (full tables) / `--tag <Name>` / "
            "`--shape <name>`, the [Conductor Registry] block injected into "
            "agents is authoritative at dispatch, and the F2/F3 exemption sets "
            "derive from each tag's `tdd_exempt`/`coverage_exempt` profile "
            "field.\n"
            "Findings:\n" + "\n".join(findings)
        )

    print("OK: watched docs carry no hand-maintained tag/shape enumeration "
          "(table or prose closed-set), Tier-1 code sites read the vocab via "
          "registry accessors, every agent that defers to the [Conductor "
          "Registry] block is injected, and every flag an agent names is "
          "surfaced by the block.")


if __name__ == "__main__":
    main()
