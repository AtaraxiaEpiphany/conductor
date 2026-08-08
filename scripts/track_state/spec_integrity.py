"""AC integrity: coverage rates + advisory gate over spec/plan/evidence.

Read-only. Computes three Acceptance-Criteria coverage rates by cross-checking:

  * ``spec.md``  — the FR/NFR/AC/TC inventory (``spec_parse.parse_spec``)
  * ``plan.md``  — ``<!-- AC-n -->`` task annotations (``plan_parse``)
  * ``track-state.json`` — ``evidence.tc_coverage`` from completed tasks

FR/NFR are inventory-only (counts): they have no traceability channel today
(no TC/plan/evidence link), so no rate is computed for them — only AC has the
channels needed for a real coverage rate.

Mirrors the WARN-only posture of ``result._evaluate_gates``: the
``ac_integrity_gate`` string is advisory — surfaced in the finalize envelope and
``quality-snapshot`` — and never blocks a task. Degrades to ``None`` rates /
``"N/A"`` gate when ``spec.md`` is absent or has no ACs, so tracks without a
formal spec are not penalized. ``ac_integrity_reason`` distinguishes the two
N/A cases: ``"spec_missing"`` (no spec.md — intentionally spec-less, clean)
vs ``"no_acs"`` (spec.md exists but no ``## Acceptance Criteria`` section — a
planner that was supposed to anchor a spec but didn't, which new-track §2.3
re-dispatches on rather than blessing). ``None`` whenever ACs are present.
"""
import os
import re
from pathlib import Path

from .core import load
from .helpers import out
from .spec_parse import parse_spec
from .plan_parse import parse_plan, collect_ac_refs
from .workflow_shapes import resolve_shape, ac_grounding_for

# A tc_coverage evidence string holds TC IDs in any separator (space/comma/newline).
_TC_ID = re.compile(r"TC-\d+\.\d+")

# A test function that GROUNDS a TC: ``def test_TC_2_1_*(…)`` (see
# plan-format-contract.md §Test↔TC Naming Link). group(1)/group(2) are the TC
# numbers, reconstructed as ``TC-{n}.{m}``; group(3) is the name suffix (e.g.
# ``_happy``), so the full function name is ``test_TC_{n}_{m}{suffix}`` (empty
# suffix for a bare ``test_TC_2_1``). The lookahead ``(?=[_(\s])`` is
# load-bearing — without it ``test_TC_2_1`` would match as a prefix of
# ``test_TC_2_10`` (yielding TC-2.1 instead of TC-2.10). ``(?:async\s+)?`` so
# ``async def test_…`` is captured too. Multi-digit-safe, like spec_parse._TC_ROW.
_TEST_TC_FN = re.compile(r"\b(?:async\s+)?def\s+test_TC_(\d+)_(\d+)(\w*)(?=[_(\s])")

# Path segments that never hold the track's own tests — skip the whole subtree.
# Applied to EVERY part so nested leakage is caught: htmlcov / a checked-in venv
# / site-packages literally contain ``def test_…`` strings that would fake-ground.
_SKIP_PARTS = {".git", ".conductor", "node_modules", "__pycache__", ".venv",
               "venv", ".tox", "build", "dist", ".eggs", "htmlcov",
               ".pytest_cache", "site-packages"}


# --- EARS (Easy Approach to Requirements Syntax) advisory lint ----------------
# Every Functional / Non-Functional requirement must carry a mandatory EARS
# response verb, and must avoid negation (``shall not`` / ``shall never``) — EARS
# §12 says rephrase as a positive ``If <trigger>, then the <system> shall
# <recovery>.`` unwanted-behavior clause rather than negating. ACs are NOT linted:
# they are measurable pass/fail criteria, not EARS requirements, and legitimately
# may not carry an obligation verb. Advisory only — authoring quality never blocks
# a task (same WARN-only posture as ``ac_integrity_gate``).
#
# Multilingual: the mandatory verb need not be English ``shall``. The canonical
# obligation modal in common spec languages is accepted too — Latin verbs
# (FR/ES/IT/PT/DE/NL) match with ``\b`` and benefit from Unicode case-folding;
# CJK verbs (ZH/JA/KO) match WITHOUT ``\b`` because Python's word boundary does
# not fire between two ideographs (``系统应当响应`` would defeat ``\b应当\b``).
# ``CONDUCTOR_EARS_VERBS`` (comma-separated) appends project-specific verbs; each
# is auto-classified into the Latin or CJK branch by the same rule, so tracks in
# any language can be made EARS-clean without a code change. Negation detection
# stays English-centric (``shall not``) — the cross-language equivalent (FR
# ``ne…pas``, DE ``nicht``) is discontinuous and would false-positive; the
# mandatory-verb axis is the load-bearing one and is fully multilingual here.
_EARS_LATIN_VERBS = (
    "shall",                              # English (canonical EARS)
    "doit", "devra", "devront",           # French
    "debe", "deberá", "deberán",          # Spanish
    "deve", "dovrà", "devono",            # Italian
    "deve", "deverá", "devem",            # Portuguese
    "muss", "müssen",                     # German (mandatory; "soll"≈should, excluded)
    "moet", "moeten",                     # Dutch
)
_EARS_CJK_VERBS = (
    "应当", "应", "必须",                                 # Chinese (Simplified)
    "しなければならない", "するものとする", "すること",    # Japanese
    "해야 한다", "한다",                                  # Korean
)


def _is_cjk_verb(verb):
    """True if ``verb`` contains a CJK ideograph / kana / hangul syllable.

    Such verbs are matched without ``\\b`` (no boundary fires between two
    ideographs); Latin-script verbs are matched with ``\\b``."""
    for c in verb:
        if ("一" <= c <= "鿿"     # CJK Unified Ideographs
                or "぀" <= c <= "ヿ"  # Hiragana + Katakana
                or "가" <= c <= "힯"):  # Hangul Syllables
            return True
    return False


def _build_ears_shall_regex():
    """Compile the mandatory-EARS-verb regex once at import.

    Latin verbs get ``\\b`` anchors (and IGNORECASE Unicode folding); CJK verbs
    do not. ``CONDUCTOR_EARS_VERBS`` extends the set; its entries are classified
    by ``_is_cjk_verb`` so an env-supplied verb lands in the right branch."""
    extra = tuple(v.strip()
                  for v in os.environ.get("CONDUCTOR_EARS_VERBS", "").split(",")
                  if v.strip())
    latin = sorted({v for v in (_EARS_LATIN_VERBS + extra) if not _is_cjk_verb(v)},
                   key=len, reverse=True)
    cjk = sorted({v for v in (_EARS_CJK_VERBS + extra) if _is_cjk_verb(v)},
                 key=len, reverse=True)
    parts = []
    if latin:
        parts.append(r"\b(?:" + "|".join(re.escape(v) for v in latin) + r")\b")
    if cjk:
        parts.append(r"(?:" + "|".join(re.escape(v) for v in cjk) + r")")
    # Unreachable in practice (shall is always present); guards a degenerate env.
    body = "|".join(parts) if parts else r"(?!)"
    return re.compile(body, re.IGNORECASE)


_EARS_SHALL = _build_ears_shall_regex()
_EARS_NEGATION = re.compile(r"\bshall\s+(?:not|never)\b|\bshan['’]t\b",
                            re.IGNORECASE)


def _covered_tcs(state):
    """Set of TC IDs reported covered by completed tasks' evidence."""
    covered = set()

    def collect(unit):
        if unit.get("status") != "completed":
            return
        raw = (unit.get("evidence") or {}).get("tc_coverage", "")
        if raw:
            covered.update(_TC_ID.findall(raw))

    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            collect(task)
            for sub in task.get("subtasks", []):
                collect(sub)
    return covered


def _resolve_grounding(spec, state):
    """How ACs are grounded for this track: ``"test"`` or ``"review"``.

    Shape-driven when ``track-state.json`` exists (the authoritative
    declaration): the resolved shape's ``ac_grounding`` field. Spec-inferred
    when state is absent (planning time — new-track §2.3 runs
    :func:`compute_ac_integrity` BEFORE §2.6 creates state): a spec carrying
    ``## Artifact Anchors`` rows is review-grounded, else test. Both paths
    fail-open to ``"test"`` (today's behavior) so a legacy track, a typo'd
    shape, or a pre-review spec never blocks. ``state`` is ``None`` when
    track-state.json is absent (the caller loads once and passes ``None``).
    """
    if state is not None:
        return ac_grounding_for(resolve_shape(state.get("workflow_shape")))
    return "review" if spec.get("anchors") else "test"


def _plan_traceability(track_dir, acs):
    """Rate 2: AC → plan traceability (grounding-agnostic).

    Every AC referenced by a task's ``<!-- AC-n -->`` annotation in plan.md.
    Shared by the test-grounded and review-grounded branches — plan↔spec
    traceability is orthogonal to HOW an AC is grounded (test vs review).
    Returns ``(ac_traceability_rate, untraced_acs, dangling_ac_refs)``; the rate
    is ``None`` when plan.md is absent (unmeasured, never 0%). Dangling refs are
    plan AC refs absent from ``acs`` (a plan that names an AC the spec doesn't).
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return None, [], []
    plan_acs = set(collect_ac_refs(parse_plan(plan_path)))
    untraced = [a for a in acs if a not in plan_acs]
    rate = round(100 * (len(acs) - len(untraced)) / len(acs), 1)
    dangling = sorted(r for r in plan_acs if r not in set(acs))
    return rate, untraced, dangling


def _attested_acs(state):
    """Set of AC IDs with a POSITIVE review attestation in completed-task evidence.

    The review-grounding twin of :func:`_covered_tcs`: instead of
    ``evidence.tc_coverage`` it reads ``evidence.review_attestations``
    (``{AC-N: {"anchor", "attested_by", "verdict"}}`` — written by the review
    verifier). An AC counts as attested iff a completed task carries it with a
    positive verdict (``pass``/``passed``/…). Absent/empty/non-dict ⇒ ``set()``
    (no ACs attested yet — correct for a fresh track; the gate never uses Rate 3,
    so a deliverable track is gated on anchor coverage + plan traceability, not
    on a review it hasn't had a chance to run yet).
    """
    attested = set()

    def collect(unit):
        if unit.get("status") != "completed":
            return
        raw = (unit.get("evidence") or {}).get("review_attestations")
        if not isinstance(raw, dict):
            return
        for ac, entry in raw.items():
            if isinstance(entry, dict) and \
                    str(entry.get("verdict", "")).lower().startswith("pass"):
                attested.add(ac)

    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            collect(task)
            for sub in task.get("subtasks", []):
                collect(sub)
    return attested


def _measured_tcs_with_locations(track_dir):
    """Map of TC ID → ``{"test", "location"}`` for every grounding test function.

    The located twin of ``_measured_tcs``: the SAME scan (same ``_SKIP_PARTS``
    subtree exclusion, same per-line ``#``-comment strip, same multi-digit-safe
    ``_TEST_TC_FN`` boundary) but iterated **per line** so each grounding records
    the function ``test`` name and a ``file:line`` location (path relative to the
    track root, posix separators). First-wins on duplicate TC IDs (a TC grounded
    in two files reports the first found — the set view hides the rest, exactly
    as before).

    Returns ``{}`` when the naming convention is unadopted — callers treat empty
    as "unmeasured" (None rate), not 0%. ``_measured_tcs`` is a thin set-view
    wrapper over this; the integrity enrichment (``ac_evidence``) reads the map.
    """
    measured = {}
    root = Path(track_dir)
    for p in root.rglob("*.py"):
        if _SKIP_PARTS & set(p.parts):
            continue
        if not (p.name.startswith("test_") or p.name.endswith("_test.py")):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        # Per-line scan so each grounding carries its ``file:line``; strip ``#``
        # comments per line so ``# def test_TC_2_1()`` can't fake grounding.
        for lineno, ln in enumerate(text.splitlines(), 1):
            stripped = re.sub(r"#.*$", "", ln)
            mt = _TEST_TC_FN.search(stripped)
            if not mt:
                continue
            n, tc_m, suffix = mt.group(1), mt.group(2), mt.group(3)
            tc = f"TC-{n}.{tc_m}"
            if tc in measured:
                continue  # first-wins
            measured[tc] = {
                "test": f"test_TC_{n}_{tc_m}{suffix}",
                "location": f"{rel}:{lineno}",
            }
    return measured


def _measured_tcs(track_dir):
    """Set of TC IDs GROUNDED by real test functions under ``track_dir``.

    Thin set-view wrapper over ``_measured_tcs_with_locations`` (same scan,
    dropping the location detail). The measured twin of ``_covered_tcs``
    (self-report): instead of trusting ``evidence.tc_coverage`` it scans
    ``test_*.py`` / ``*_test.py`` for ``def test_TC_{n}_{m}`` functions
    (plan-format-contract.md §Test↔TC Naming Link) and reconstructs
    ``TC-{n}.{m}``. Skips vendored/generated subtrees via ``_SKIP_PARTS`` and
    strips ``#`` comments so a commented-out test function can't fake grounding.
    Returns ``set()`` when the convention is unadopted — callers treat empty as
    "unmeasured" (None rate), not 0%.
    """
    return set(_measured_tcs_with_locations(track_dir).keys())


def _verified_acs(acs, tc_to_ac, covered):
    """Partition ``acs`` by whether all their TCs are in ``covered``.

    Returns ``(verified, partial, unverified)``. ACs with no TCs count as
    unverified (also orphans, counted under Rate 1). Shared by the self-report
    Rate 3 (``_covered_tcs``) and the measured twin (``_measured_tcs``) so the
    two twins classify *identically* and differ only in the TC source — the
    gap between them IS the self-report-vs-measured signal.
    """
    verified, partial, unverified = [], [], []
    for ac in acs:
        ac_tcs = [tc for tc, a in tc_to_ac.items() if a == ac]
        if not ac_tcs:
            unverified.append(ac)
            continue
        hit = sum(1 for tc in ac_tcs if tc in covered)
        if hit == len(ac_tcs):
            verified.append(ac)
        elif hit:
            partial.append(ac)
        else:
            unverified.append(ac)
    return verified, partial, unverified


def compute_ac_evidence_map(acs, tc_to_ac, covered, measured_map):
    """Per-AC evidence trace: for each AC, list its TCs with a grounding status.

    Status per TC (measured wins over claimed, so a real test always overrides
    a self-report):

      * ``measured`` — a real ``def test_TC_{n}_{m}`` grounds it; carries the
        ``test`` name and ``location`` (``file:line``) from ``measured_map``.
      * ``claimed`` — present in a completed task's ``evidence.tc_coverage``
        (``covered``) but NOT grounded by a named test (agent claims, didn't
        write the named test — the self-report-inflation signal).
      * ``missing`` — neither measured nor claimed.

    ACs with no TCs (orphans, flagged under Rate 1) carry an empty ``tcs``
    list. Pure function over its inputs — no FS access — so it is unit-testable
    without a track dir. Used by ``compute_ac_integrity`` to enrich the result
    with the additive ``ac_evidence`` key; the integrity gate is unchanged.
    """
    out = []
    for ac in acs:
        ac_tcs = [tc for tc, a in tc_to_ac.items() if a == ac]
        entries = []
        for tc in ac_tcs:
            if tc in measured_map:
                entries.append({
                    "id": tc, "status": "measured",
                    "test": measured_map[tc]["test"],
                    "location": measured_map[tc]["location"],
                })
            elif tc in covered:
                entries.append({"id": tc, "status": "claimed"})
            else:
                entries.append({"id": tc, "status": "missing"})
        out.append({"ac": ac, "tcs": entries})
    return out


def compute_review_ac_evidence_map(acs, anchors, attested):
    """Per-AC evidence trace for REVIEW grounding (the review twin of
    :func:`compute_ac_evidence_map`). Each AC carries its declared artifact
    anchor + an attestation status:

      * ``attested`` — a completed task's evidence carries a POSITIVE review
        attestation for this AC (the grounding artifact — written by the review
        verifier, B4).
      * ``unattested`` — the AC has a declared anchor but no positive
        attestation yet (the deliverable exists; the review hasn't signed off).
      * ``orphan`` — the AC has no declared anchor at all (a Rate-1 orphan).

    Pure function over its inputs (no FS) so it is unit-testable without a track
    dir. ``attested`` is the set of AC IDs with a positive attestation
    (:func:`_attested_acs`). Used by ``compute_ac_integrity`` (review branch) to
    enrich the result with the additive ``ac_evidence`` key; the gate is
    unchanged.
    """
    anchor_by_ac = {a["ac"]: a for a in anchors}
    out = []
    for ac in acs:
        if ac in anchor_by_ac:
            a = anchor_by_ac[ac]
            out.append({
                "ac": ac,
                "anchor": a.get("artifact", ""),
                "location": a.get("location", ""),
                "status": "attested" if ac in attested else "unattested",
            })
        else:
            out.append({"ac": ac, "anchor": "", "location": "",
                        "status": "orphan"})
    return out


def _empty(fr_count=0, nfr_count=0, reason=None):
    """Degraded result: no ACs to rate (no spec.md, or spec has no ACs).

    ``reason`` (``"spec_missing"`` / ``"no_acs"`` / ``None``) lets callers
    distinguish *intentionally* spec-less tracks (``spec_missing`` — clean) from
    a planner that was supposed to produce a spec but wrote no AC section
    (``no_acs`` — the weak-model anchor-drift failure new-track §2.3 must
    re-dispatch on). ``None`` on the measured/PASS paths where the gate carries
    the signal.
    """
    return {
        "ac_count": 0,
        "tc_count": 0,
        "anchor_count": 0,
        "fr_count": fr_count,
        "nfr_count": nfr_count,
        "ac_grounding": "test",
        "ac_tc_coverage_rate": None,
        "ac_traceability_rate": None,
        "ac_verification_rate": None,
        "ac_verification_measured_rate": None,
        "orphan_acs": [],
        "untraced_acs": [],
        "dangling_ac_refs": [],
        "unverified_acs": [],
        "partial_acs": [],
        "spec_errors": [],
        "ac_integrity_gate": "N/A",
        "ac_integrity_reason": reason,
        "ac_evidence": [],
        "ears_warnings": [],
        "ears_gate": "N/A",
    }


def _gate(ac_tc_coverage_rate, orphan_acs, ac_traceability_rate, untraced_acs,
          dangling_ac_refs, grounding="test"):
    """PASS iff every AC has a grounding substrate (a TC for test shapes, an
    artifact anchor for review shapes), every AC is traced to a task, and no
    plan AC ref dangles (references an AC absent from spec). Verification is
    reported separately and NOT gated on — evidence is best-effort.

    The FAILED string names the offending AC IDs and appends a per-problem fix
    clause, so the message closes the feedback loop on its own (verdict + fix in
    one string — the contract the blocking hooks already use). The verdict
    prefix and the "without a TC"/"without an artifact anchor"/"untraced in
    plan"/"dangling" substrings are preserved for prefix/substring matching. The
    coverage fix clause branches on ``grounding`` (Test Scenarios vs Artifact
    Anchors); traceability + dangling are grounding-agnostic."""
    problems = []
    fixes = []
    if ac_tc_coverage_rate is not None and ac_tc_coverage_rate < 100.0:
        if grounding == "review":
            problems.append(f"{len(orphan_acs)} AC(s) without an artifact anchor: {', '.join(orphan_acs)}")
            fixes.append("add an `AC-{n} | <artifact> | <location>` row under "
                         "## Artifact Anchors in spec.md for each orphan AC")
        else:
            problems.append(f"{len(orphan_acs)} AC(s) without a TC: {', '.join(orphan_acs)}")
            fixes.append("add a `TC-{n}.{m} | AC-{n} | ...` row under ## Test "
                         "Scenarios in spec.md for each orphan AC")
    if ac_traceability_rate is not None and ac_traceability_rate < 100.0:
        problems.append(f"{len(untraced_acs)} AC(s) untraced in plan: {', '.join(untraced_acs)}")
        fixes.append("annotate the implementing task in plan.md with a "
                     "`<!-- AC-n -->` comment for each untraced AC")
    if dangling_ac_refs:
        problems.append(f"{len(dangling_ac_refs)} dangling plan AC ref(s): {', '.join(dangling_ac_refs)}")
        fixes.append("remove the dangling AC-n ref(s) from plan.md, or add "
                     "the missing AC-n to spec.md")
    if not problems:
        return "PASS"
    return "FAILED (" + "; ".join(problems) + ") — fix: " + "; ".join(fixes)


def _ears_item_warnings(item):
    """Return a reason string if requirement ``item`` (``{"id","text"}``) breaks an
    EARS invariant, else ``None``. Missing mandatory verb takes priority; a
    requirement that carries the verb but negates it (``shall not``) gets the
    negation reason. The mandatory verb may be English ``shall`` or any localized
    equivalent in ``_EARS_SHALL`` (extend via ``CONDUCTOR_EARS_VERBS``)."""
    text = item.get("text", "")
    if not _EARS_SHALL.search(text):
        return ("missing mandatory EARS response verb — EARS requires an "
                "obligation modal (e.g. 'shall'/'doit'/'muss'/'应当'; extend via "
                "CONDUCTOR_EARS_VERBS; avoid should/may/will)")
    if _EARS_NEGATION.search(text):
        return ("negation — rephrase as a positive 'If <trigger>, then the "
                "<system> shall <recovery>.' unwanted-behavior clause "
                "(avoid 'shall not')")
    return None


def _ears_warnings(items):
    """List of ``{"id","reason"}`` for requirements breaking an EARS invariant."""
    out = []
    for it in items:
        reason = _ears_item_warnings(it)
        if reason:
            out.append({"id": it.get("id", "?"), "reason": reason})
    return out


def _ears_gate_str(warnings):
    """``PASS`` / ``WARN(...)`` advisory string. WARN names the offending IDs and
    appends a one-clause fix — the same verdict+fix-in-one-string contract
    ``_gate`` uses, so the message closes the feedback loop on its own."""
    if not warnings:
        return "PASS"
    ids = ", ".join(w["id"] for w in warnings)
    return (f"WARN ({len(warnings)} requirement(s) not EARS-compliant: {ids}) — "
            "fix: rewrite each in an EARS pattern with a mandatory response verb "
            "('shall' or a localized equivalent: doit/debe/deve/muss/moet/应当 …; "
            "extend via CONDUCTOR_EARS_VERBS); When/While/Where/If-then, or "
            "ubiquitous 'The <system> shall ...'; see spec-scaffold.md "
            "Requirements (EARS)")


def compute_ac_integrity(track_dir):
    """Compute the AC integrity dict for a track (read-only).

    Returns the full inventory + rates + diagnostic lists + ``ac_integrity_gate``.
    Never returns ``None``; callers that must not crash (finalize paths) use
    ``_ac_integrity_gate`` which wraps this in a try/except.
    """
    spec_path = Path(track_dir) / "spec.md"
    if not spec_path.exists():
        return _empty(reason="spec_missing")

    spec = parse_spec(spec_path)
    # EARS advisory lint over FR/NFR (ACs are criteria, not EARS requirements) —
    # independent of AC coverage, so computed even when the spec has no ACs.
    ears_warn = _ears_warnings(spec["fr_items"] + spec["nfr_items"])
    acs = sorted(set(spec["acs"]))
    if not acs:
        base = _empty(fr_count=len(set(spec["frs"])),
                      nfr_count=len(set(spec["nfrs"])),
                      reason="no_acs")
        base["ears_warnings"] = ears_warn
        base["ears_gate"] = _ears_gate_str(ears_warn)
        return base

    # Load state once (None when track-state.json is absent — planning time:
    # new-track §2.3 runs this BEFORE §2.6 creates state). Reused for grounding
    # resolution AND Rate 3, so there is no second read of track-state.json.
    try:
        state = load(track_dir)
    except FileNotFoundError:
        state = None

    # How ACs are grounded: shape-driven when state exists (the authoritative
    # declaration); spec-inferred (## Artifact Anchors rows present) when state
    # is absent. Fail-open to "test" so a legacy track / a typo / a pre-review
    # spec never blocks. The review branch is its own computation; everything
    # below is the test-grounded path today's tracks run unchanged.
    grounding = _resolve_grounding(spec, state)
    if grounding == "review":
        return _compute_review_integrity(track_dir, spec, acs, ears_warn, state)

    tc_to_ac = spec["tc_to_ac"]
    # --- Rate 1 (test grounding): AC → TC coverage (every AC has ≥1 TC in the
    # Test Scenarios table)
    acs_with_tc = {a for a in tc_to_ac.values()}
    orphan_acs = [a for a in acs if a not in acs_with_tc]
    ac_tc_coverage_rate = round(100 * (len(acs) - len(orphan_acs)) / len(acs), 1)

    # --- Rate 2: AC → plan traceability (grounding-agnostic — shared with the
    # review branch via _plan_traceability).
    ac_traceability_rate, untraced_acs, dangling_ac_refs = _plan_traceability(
        track_dir, acs)

    # --- Rate 3 (self-report): AC verification (all its TCs in completed-task
    # evidence.tc_coverage — what the agent CLAIMS). Reported, NOT gated. No
    # state (planning time) ⇒ no completed-task evidence ⇒ an empty covered set
    # (Rate 3 self-report is 0%, which is correct — the gate uses only Rate 1/2,
    # so a fresh track is gated on AC→TC + AC→plan traceability, not on
    # verification it hasn't had a chance to do yet).
    covered = _covered_tcs(state or {})
    verified, partial, unverified = _verified_acs(acs, tc_to_ac, covered)
    ac_verification_rate = round(100 * len(verified) / len(acs), 1)

    # --- Rate 3 measured twin: same classification over REAL test functions
    # (def test_TC_{n}_{m}, scanned by _measured_tcs) — what's actually GROUNDED.
    # Unlike the self-report twin, this is None (unmeasured) when the convention
    # is unadopted: the denominators differ — self-report has one (completed-task
    # evidence is always present), measured has none until tests follow the
    # naming link. So ac_verification_rate can be a real 0.0 while this is None;
    # that gap is the "agent claims but didn't write named tests" signal.
    measured_map = _measured_tcs_with_locations(track_dir)
    measured = set(measured_map)
    if measured:
        verified_m, _partial_m, _unverified_m = _verified_acs(
            acs, tc_to_ac, measured)
        ac_verification_measured_rate = round(100 * len(verified_m) / len(acs), 1)
    else:
        ac_verification_measured_rate = None

    # Per-AC evidence trace (completeness-critic substrate): each AC's TCs with a
    # measured/claimed/missing grounding status. Additive — the integrity gate is
    # unchanged; phase-checker / new-track surface this to close the "phase passes
    # L1/L2 while an AC is never grounded" hole.
    ac_evidence = compute_ac_evidence_map(acs, tc_to_ac, covered, measured_map)

    return {
        "ac_count": len(acs),
        "tc_count": len(tc_to_ac),
        "anchor_count": len(spec["anchors"]),
        "fr_count": len(set(spec["frs"])),
        "nfr_count": len(set(spec["nfrs"])),
        "ac_grounding": "test",
        "ac_tc_coverage_rate": ac_tc_coverage_rate,
        "ac_traceability_rate": ac_traceability_rate,
        "ac_verification_rate": ac_verification_rate,
        "ac_verification_measured_rate": ac_verification_measured_rate,
        "orphan_acs": orphan_acs,
        "untraced_acs": untraced_acs,
        "dangling_ac_refs": dangling_ac_refs,
        "unverified_acs": unverified,
        "partial_acs": partial,
        "spec_errors": spec.get("errors", []),
        "ac_integrity_gate": _gate(
            ac_tc_coverage_rate, orphan_acs, ac_traceability_rate,
            untraced_acs, dangling_ac_refs, grounding="test"),
        "ac_integrity_reason": None,
        "ac_evidence": ac_evidence,
        "ears_warnings": ears_warn,
        "ears_gate": _ears_gate_str(ears_warn),
    }


def _compute_review_integrity(track_dir, spec, acs, ears_warn, state):
    """Review-grounded AC integrity (the deliverable-shape branch).

    ACs are grounded by an **artifact anchor + a review attestation**, not by
    test functions. So Rate 1 becomes AC→anchor coverage (every AC has a
    ``## Artifact Anchors`` row), and Rate 3 becomes AC→attestation (a positive
    verdict in a completed task's ``evidence.review_attestations``). Rate 2 (plan
    traceability) is grounding-agnostic — shared with the test branch via
    :func:`_plan_traceability`. There is no measured twin in review mode: the
    attestation IS the grounding artifact (there is no "claimed vs real test"
    gap — a review either attested the AC or it didn't), so
    ``ac_verification_measured_rate`` is ``None``.

    ``state`` is the already-loaded track state (``None`` at planning time) —
    passed in by :func:`compute_ac_integrity` so this never re-reads
    track-state.json. The rate keys reuse the test-branch names (``ac_tc_*``)
    so consumers stay schema-identical; ``ac_grounding: "review"`` is the honest
    signal that the "tc coverage" rate is anchor coverage.
    """
    anchors = spec["anchors"]
    acs_with_anchor = {a["ac"] for a in anchors}
    orphan_acs = [a for a in acs if a not in acs_with_anchor]
    ac_tc_coverage_rate = round(100 * (len(acs) - len(orphan_acs)) / len(acs), 1)

    ac_traceability_rate, untraced_acs, dangling_ac_refs = _plan_traceability(
        track_dir, acs)

    attested = _attested_acs(state or {})
    verified = [a for a in acs if a in attested]
    unverified = [a for a in acs if a not in attested]
    ac_verification_rate = round(100 * len(verified) / len(acs), 1)
    ac_verification_measured_rate = None  # no measured twin in review mode

    ac_evidence = compute_review_ac_evidence_map(acs, anchors, attested)

    return {
        "ac_count": len(acs),
        "tc_count": len(spec["tcs"]),  # 0 for a review spec (literal, not anchors)
        "anchor_count": len(anchors),
        "fr_count": len(set(spec["frs"])),
        "nfr_count": len(set(spec["nfrs"])),
        "ac_grounding": "review",
        "ac_tc_coverage_rate": ac_tc_coverage_rate,
        "ac_traceability_rate": ac_traceability_rate,
        "ac_verification_rate": ac_verification_rate,
        "ac_verification_measured_rate": ac_verification_measured_rate,
        "orphan_acs": orphan_acs,
        "untraced_acs": untraced_acs,
        "dangling_ac_refs": dangling_ac_refs,
        "unverified_acs": unverified,
        "partial_acs": [],
        "spec_errors": spec.get("errors", []),
        "ac_integrity_gate": _gate(
            ac_tc_coverage_rate, orphan_acs, ac_traceability_rate,
            untraced_acs, dangling_ac_refs, grounding="review"),
        "ac_integrity_reason": None,
        "ac_evidence": ac_evidence,
        "ears_warnings": ears_warn,
        "ears_gate": _ears_gate_str(ears_warn),
    }


def cmd_spec_anchors(track_dir):
    """Read-only structural check: are the English machine anchors present?

    Guards the weak-model failure where ``spec.md`` is written as free-form
    narrative (often in another language) with no ``## Acceptance Criteria``
    section or grounding substrate — so ``compute_ac_integrity`` degrades to
    ``N/A`` and the new-track §2.3 loop blesses it as clean. This check asserts
    the *structure* the parser needs, reusing ``parse_spec`` (no parallel scan):
    every spec a planner produces MUST carry ``- AC-N:`` bullets under ``##
    Acceptance Criteria`` AND a grounding substrate — either ``| TC-N.M | AC-N
    |`` rows under ``## Test Scenarios`` (test-grounded) OR ``| AC-N | … |`` rows
    under ``## Artifact Anchors`` (review-grounded, non-code). Either substrate
    satisfies the check; a spec with ACs but NEITHER fails.

    Language-agnostic by design: it checks English anchor *tokens*, not prose.
    A fully-Chinese-prose spec with the headings + ``AC-N``/``TC-N.M`` IDs
    passes; only the bracketed body text may be any language. FR/NFR are
    inventory-only (no traceability channel), so they are NOT gated.

    Output mirrors ``init-from-plan --check``: ``ok`` (bool) + ``errors[]``
    (each a verdict+fix clause) + ``source`` + counts. Always exits 0;
    failure is surfaced via ``ok:false``, never a non-zero process exit.
    """
    spec_path = Path(track_dir) / "spec.md"
    if not spec_path.exists():
        out(dict(ok=False, errors=[f"spec.md not found at {spec_path}"]))
        return

    spec = parse_spec(spec_path)
    errors = []
    if not spec["acs"]:
        errors.append(
            "missing '## Acceptance Criteria' section (or no '- AC-N:' bullets) "
            "— the heading and AC IDs are machine anchors; keep them in English "
            "even when the prose is another language")
    if spec["acs"] and not (spec["tcs"] or spec["anchors"]):
        errors.append(
            "missing '## Test Scenarios' table (or no '| TC-N.M | AC-N |' rows) "
            "OR '## Artifact Anchors' table (or no '| AC-N | … |' rows) — a "
            "test-grounded spec carries Test Scenarios; a review-grounded "
            "(non-code) spec carries Artifact Anchors in their place. Both are "
            "machine anchors; keep the IDs in English even when the prose is "
            "another language")
    out(dict(ok=not errors, check=True, source=str(spec_path),
             ac_count=len(spec["acs"]), tc_count=len(spec["tcs"]),
             anchor_count=len(spec["anchors"]), errors=errors))


def _ac_integrity_gates(track_dir):
    """``(ac_integrity_gate, ears_gate)`` advisory strings from ONE snapshot.

    Both gates derive from a single ``compute_ac_integrity`` call (parse_spec +
    parse_plan + load + the measured-TC ``*.py`` scan). The two single-gate
    helpers below each used to pay that full cost in isolation — fine alone, but
    the two finalize paths need *both* strings back-to-back, so computing the
    snapshot twice per finalize was pure waste (a doubled spec/plan parse and a
    doubled scan of the track's test tree). Never raises; returns
    ``("N/A", "N/A")`` on any error so a gate status never blocks completion.
    """
    try:
        snap = compute_ac_integrity(track_dir)
        return snap.get("ac_integrity_gate", "N/A"), snap.get("ears_gate", "N/A")
    except Exception:
        return "N/A", "N/A"


def _ac_integrity_gate(track_dir):
    """Advisory track-level AC-integrity gate string. Never raises.

    Thin single-element view over ``_ac_integrity_gates`` for callers (and
    tests) that need only this one signal. WARN-only: computed after
    completion, never blocks.
    """
    return _ac_integrity_gates(track_dir)[0]


def _ears_gate(track_dir):
    """Advisory track-level EARS gate string. Never raises.

    Thin single-element view over ``_ac_integrity_gates``. WARN-only:
    requirement quality never blocks a task.
    """
    return _ac_integrity_gates(track_dir)[1]
