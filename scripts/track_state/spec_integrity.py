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
import re
from pathlib import Path

from .core import load
from .spec_parse import parse_spec
from .plan_parse import parse_plan, collect_ac_refs

# A tc_coverage evidence string holds TC IDs in any separator (space/comma/newline).
_TC_ID = re.compile(r"TC-\d+\.\d+")

# A test function that GROUNDS a TC: ``def test_TC_2_1_*(…)`` (see
# plan-format-contract.md §Test↔TC Naming Link). group(1)/group(2) are the TC
# numbers, reconstructed as ``TC-{n}.{m}``. The lookahead ``(?=[_(\s])`` is
# load-bearing — without it ``test_TC_2_1`` would match as a prefix of
# ``test_TC_2_10`` (yielding TC-2.1 instead of TC-2.10). ``(?:async\s+)?`` so
# ``async def test_…`` is captured too. Multi-digit-safe, like spec_parse._TC_ROW.
_TEST_TC_FN = re.compile(r"\b(?:async\s+)?def\s+test_TC_(\d+)_(\d+)(?=[_(\s])")

# Path segments that never hold the track's own tests — skip the whole subtree.
# Applied to EVERY part so nested leakage is caught: htmlcov / a checked-in venv
# / site-packages literally contain ``def test_…`` strings that would fake-ground.
_SKIP_PARTS = {".git", ".conductor", "node_modules", "__pycache__", ".venv",
               "venv", ".tox", "build", "dist", ".eggs", "htmlcov",
               ".pytest_cache", "site-packages"}


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


def _measured_tcs(track_dir):
    """Set of TC IDs GROUNDED by real test functions under ``track_dir``.

    The measured twin of ``_covered_tcs`` (self-report): instead of trusting
    ``evidence.tc_coverage`` it scans ``test_*.py`` / ``*_test.py`` for
    ``def test_TC_{n}_{m}`` functions (plan-format-contract.md §Test↔TC Naming
    Link) and reconstructs ``TC-{n}.{m}``. Skips vendored/generated subtrees
    via ``_SKIP_PARTS`` and strips ``#`` comments so a commented-out test
    function can't fake grounding. Returns ``set()`` when the convention is
    unadopted — callers treat empty as "unmeasured" (None rate), not 0%.
    """
    measured = set()
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
        # Strip ``#`` comments so ``# def test_TC_2_1()`` can't fake grounding.
        stripped = "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())
        for n, m in _TEST_TC_FN.findall(stripped):
            measured.add(f"TC-{n}.{m}")
    return measured


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
    acs = sorted(set(spec["acs"]))
    tc_to_ac = spec["tc_to_ac"]
    if not acs:
        return _empty(fr_count=len(set(spec["frs"])),
                      nfr_count=len(set(spec["nfrs"])))

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
    covered = _covered_tcs(load(track_dir))
    verified, partial, unverified = _verified_acs(acs, tc_to_ac, covered)
    ac_verification_rate = round(100 * len(verified) / len(acs), 1)

    # --- Rate 3 measured twin: same classification over REAL test functions
    # (def test_TC_{n}_{m}, scanned by _measured_tcs) — what's actually GROUNDED.
    # Unlike the self-report twin, this is None (unmeasured) when the convention
    # is unadopted: the denominators differ — self-report has one (completed-task
    # evidence is always present), measured has none until tests follow the
    # naming link. So ac_verification_rate can be a real 0.0 while this is None;
    # that gap is the "agent claims but didn't write named tests" signal.
    measured = _measured_tcs(track_dir)
    if measured:
        verified_m, _partial_m, _unverified_m = _verified_acs(
            acs, tc_to_ac, measured)
        ac_verification_measured_rate = round(100 * len(verified_m) / len(acs), 1)
    else:
        ac_verification_measured_rate = None

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
    }


def _ac_integrity_gate(track_dir):
    """Advisory track-level AC-integrity gate string. Never raises.

    Shared by both finalize paths (process-result + dispatch-finalize) so the
    signal cannot drift between them — mirrors how ``result._evaluate_gates`` is
    shared. WARN-only: computed after completion, never blocks.
    """
    try:
        return compute_ac_integrity(track_dir).get("ac_integrity_gate", "N/A")
    except Exception:
        return "N/A"
