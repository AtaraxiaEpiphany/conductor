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
formal spec are not penalized.
"""
import os
import re
from pathlib import Path

from .core import load
from .spec_parse import parse_spec
from .plan_parse import parse_plan, collect_ac_refs

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


def _empty(fr_count=0, nfr_count=0):
    """Degraded result: no ACs to rate (no spec.md, or spec has no ACs)."""
    return {
        "ac_count": 0,
        "tc_count": 0,
        "fr_count": fr_count,
        "nfr_count": nfr_count,
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
        "ac_evidence": [],
        "ears_warnings": [],
        "ears_gate": "N/A",
    }


def _gate(ac_tc_coverage_rate, orphan_acs, ac_traceability_rate, untraced_acs,
          dangling_ac_refs):
    """PASS iff every AC has a TC, every AC is traced to a task, and no plan AC
    ref dangles (references an AC absent from spec). Verification is reported
    separately and NOT gated on — evidence.tc_coverage is best-effort.

    The FAILED string names the offending AC IDs and appends a per-problem fix
    clause, so the message closes the feedback loop on its own (verdict + fix in
    one string — the contract the blocking hooks already use). The verdict
    prefix and the "without a TC"/"untraced in plan"/"dangling" substrings are
    preserved for prefix/substring matching."""
    problems = []
    fixes = []
    if ac_tc_coverage_rate is not None and ac_tc_coverage_rate < 100.0:
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
        return _empty()

    spec = parse_spec(spec_path)
    # EARS advisory lint over FR/NFR (ACs are criteria, not EARS requirements) —
    # independent of AC coverage, so computed even when the spec has no ACs.
    ears_warn = _ears_warnings(spec["fr_items"] + spec["nfr_items"])
    acs = sorted(set(spec["acs"]))
    tc_to_ac = spec["tc_to_ac"]
    if not acs:
        base = _empty(fr_count=len(set(spec["frs"])),
                      nfr_count=len(set(spec["nfrs"])))
        base["ears_warnings"] = ears_warn
        base["ears_gate"] = _ears_gate_str(ears_warn)
        return base

    # --- Rate 1: AC → TC coverage (every AC has ≥1 TC in the Test Scenarios table)
    acs_with_tc = {a for a in tc_to_ac.values()}
    orphan_acs = [a for a in acs if a not in acs_with_tc]
    ac_tc_coverage_rate = round(100 * (len(acs) - len(orphan_acs)) / len(acs), 1)

    # --- Rate 2: AC → plan traceability (every AC referenced by a task's comment)
    plan_path = Path(track_dir) / "plan.md"
    ac_traceability_rate = None
    untraced_acs = []
    dangling_ac_refs = []
    if plan_path.exists():
        plan_acs = set(collect_ac_refs(parse_plan(plan_path)))
        untraced_acs = [a for a in acs if a not in plan_acs]
        ac_traceability_rate = round(
            100 * (len(acs) - len(untraced_acs)) / len(acs), 1)
        dangling_ac_refs = sorted(r for r in plan_acs if r not in set(acs))

    # --- Rate 3 (self-report): AC verification (all its TCs in completed-task
    # evidence.tc_coverage — what the agent CLAIMS). Reported, NOT gated.
    # track-state.json may not exist yet at planning time (new-track §2.3 runs
    # this BEFORE §2.6 creates state). No state ⇒ no completed-task evidence ⇒
    # an empty covered set (Rate 3 self-report is 0%, which is correct — the
    # gate uses only Rate 1/2, so a fresh track is gated on AC→TC + AC→plan
    # traceability, not on verification it hasn't had a chance to do yet).
    try:
        covered = _covered_tcs(load(track_dir))
    except FileNotFoundError:
        covered = set()
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
        "fr_count": len(set(spec["frs"])),
        "nfr_count": len(set(spec["nfrs"])),
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
            untraced_acs, dangling_ac_refs),
        "ac_evidence": ac_evidence,
        "ears_warnings": ears_warn,
        "ears_gate": _ears_gate_str(ears_warn),
    }


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
