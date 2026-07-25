"""Frozen anchor (feature-list) CLI — the Goodhart counter-anchor for F3.

The frozen anchor (``<track_dir>/.conductor/feature-list.json``) is the
exogenous reference the coverage gate is anchored against — the Anthropic
``feature_list.json`` pattern adapted to Conductor's AC/TC convention. Each
frozen feature pins an ``assertion_contract`` (what "passes" means, the field
the measured AC twin cannot see today — it checks a test is *named* right, not
that it *asserts* the right thing) and a ``test_locator`` (``path::test_fn``).
Only the ``passes`` field may flip during a run; the rest is frozen.

Why this exists: ``coverage_pct`` is self-reported and the measured AC twin
verifies *naming*, so coverage can be driven to 80% by weakening, skipping, or
deleting the anchoring tests (see ``on-anchor-write-guard.py``). The frozen
list is the immutable baseline the integrity rates should be recomputed
against — otherwise a migration that drops an AC reports 100% integrity
against the weakened spec. This module writes/amends it; the write-guard
enforces that the executor never edits it directly.

Commands
--------
- ``freeze <track-dir> [--force]`` — seed the list from the spec's AC/TC
  inventory + the measured grounding tests (``spec_integrity``). Refuses an
  existing list without ``--force`` (idempotent — re-freezing silently would
  launder a weakened spec into the anchor).
- ``thaw <track-dir> --locator <p> | --feature <F-AC-n> --reason <why>``
  — governed removal of one feature, appending an ``audit`` entry (target
  changes are recorded, never silent — the "references have owners" rule).
- ``anchor-status <track-dir>`` — read-only view (features, frozen_at, audit),
  consumed later by the counter-metric (Piece 3).

Invariants mirror ``brief.py``: the skill never hand-edits the JSON; it calls
these commands. Tolerant readers, idempotent, ``.conductor/`` mkdir on write.
"""
import json
import sys
from pathlib import Path

from .helpers import out
from .spec_integrity import compute_ac_integrity

_ANCHOR_FILENAME = "feature-list.json"


def _anchor_path(track_dir):
    """Pure path to the anchor file — does NOT mkdir (read never creates dirs)."""
    return Path(track_dir) / ".conductor" / _ANCHOR_FILENAME


def _anchor_read(track_dir):
    """Tolerant reader: returns the parsed dict, or ``None`` if absent/corrupt."""
    path = _anchor_path(track_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _anchor_write(track_dir, data):
    """Write the whole anchor dict, creating ``.conductor/`` if needed."""
    cdir = Path(track_dir) / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    _anchor_path(track_dir).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _now_iso():
    """ISO-8601 UTC stamp (kept local so the module is testable without a clock)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spec_head_sha(track_dir):
    """Best-effort SHA of the spec snapshot the freeze captures (audit provenance).

    Returns ``None`` on any failure — the field is provenance, not load-bearing;
    a missing SHA must never block a freeze.
    """
    try:
        import subprocess

        r = subprocess.run(
            ["git", "-C", str(track_dir), "rev-parse", "--short=10", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            sha = r.stdout.strip()
            return sha or None
    except Exception:
        pass
    return None


def cmd_freeze(track_dir, force=False):
    """Seed the frozen anchor list from the spec's AC/TC inventory.

    Reuses ``spec_integrity.compute_ac_integrity`` (the AC→TC map + diagnostics)
    and ``_measured_tcs_with_locations`` (real ``test_TC_{n}_{m}`` functions →
    ``path::test_fn`` locators) so the anchor is seeded from the SAME parsers
    the integrity rates use — no second parser to drift.

    A feature is frozen per AC: its TCs' measured locators become the pinned
    ``test_locator`` set; TCs with no measured grounding are frozen with a
    locator of ``None`` (and ``strength: "ungrounded"``) so the gap is visible,
    not hidden. Refuses an existing list without ``--force``.
    """
    if _anchor_read(track_dir) is not None and not force:
        out(dict(
            ok=False,
            error=(
                "feature-list.json already exists for this track. Re-freezing "
                "would launder the current (possibly weakened) spec into the "
                "anchor. To re-freeze deliberately, pass --force; to amend one "
                "feature, use `thaw`."
            ),
        ))
        return

    # On --force, carry the prior audit history forward so a re-freeze appends
    # rather than resets it (the audit graph must stay complete across re-freezes).
    prior_audit = []
    if force:
        existing = _anchor_read(track_dir)
        if isinstance(existing, dict):
            prior_audit = list(existing.get("audit", []) or [])

    ac = compute_ac_integrity(track_dir)
    # ``ac_evidence`` is the per-AC trace: ``[{ac, tcs:[{id,status,test,location}]}]``.
    # It already fuses the spec's TC inventory with the measured grounding tests
    # (status "measured" carries test+location), so the anchor is seeded from the
    # SAME fusion the integrity rates use — no second parser to drift.
    ac_evidence = ac.get("ac_evidence", []) or []

    features = []
    for entry in ac_evidence:
        ac_id = entry.get("ac") if isinstance(entry, dict) else None
        if not ac_id:
            continue
        locators, ungrounded = [], []
        for tc in entry.get("tcs", []) or []:
            tc_id = tc.get("id")
            if not tc_id:
                continue
            if tc.get("status") == "measured" and tc.get("location") and tc.get("test"):
                rel = tc["location"].split(":", 1)[0]
                locators.append(f"{rel}::{tc['test']}")
            else:
                ungrounded.append(tc_id)
        features.append({
            "id": f"F-{ac_id}",
            "ac_ref": ac_id,
            "tc_refs": [tc.get("id") for tc in (entry.get("tcs", []) or []) if tc.get("id")],
            "description": "",
            # The load-bearing semantic anchor — what "passes" means. Left
            # blank for the author to fill (freeze captures the STRUCTURE; the
            # contract is human judgment, which is the whole point: it must
            # stay exogenous).
            "assertion_contract": "",
            "test_locators": locators,
            "ungrounded_tcs": ungrounded,
            "strength": "ungrounded" if ungrounded and not locators else "strong",
            "passes": "unknown",
        })

    data = {
        "track_id": _track_id(track_dir),
        "frozen_at": _now_iso(),
        "frozen_from_spec_sha": _spec_head_sha(track_dir),
        "features": features,
        "audit": prior_audit + [{
            "at": _now_iso(),
            "action": "freeze",
            "count": len(features),
            "force": bool(force),
        }],
    }
    _anchor_write(track_dir, data)
    out(dict(
        ok=True,
        frozen_features=len(features),
        grounded=sum(1 for f in features if f["test_locators"]),
        ungrounded=sum(1 for f in features if not f["test_locators"]),
        frozen_at=data["frozen_at"],
        note=(
            "Anchor frozen. assertion_contract fields are intentionally blank — "
            "fill each with the semantic check the AC requires (the one field "
            "the measured AC twin cannot see). The write-guard now denies "
            "direct edits to this file and to the pinned tests."
            if features else
            "Anchor frozen with 0 features (spec has no ACs / no measured TCs)."
        ),
    ))


def cmd_thaw(track_dir, locator=None, feature=None, reason=None):
    """Governed removal of one feature from the anchor, recorded in ``audit``.

    Either ``--locator`` (a ``path::test_fn``) or ``--feature`` (an ``F-AC-n``
    id) selects the target; ``--reason`` is required (no silent thaws — a
    weakened anchor must leave an audit trail). The feature is marked
    ``thawed: true`` rather than deleted, so the audit graph stays complete.
    """
    if not reason or not reason.strip():
        out(dict(error="thaw requires --reason (target changes are recorded, never silent)"))
        return
    data = _anchor_read(track_dir)
    if data is None:
        out(dict(error="no feature-list.json for this track — nothing to thaw"))
        return

    hits = []
    for feat in data.get("features", []):
        if feature and feat.get("id") == feature:
            hits.append(feat)
        elif locator and locator in (feat.get("test_locators") or []):
            hits.append(feat)
    if not hits:
        out(dict(error=f"no frozen feature matches --locator/--feature ({locator or feature})"))
        return

    for feat in hits:
        feat["thawed"] = True
        feat["thaw_reason"] = reason.strip()
    data.setdefault("audit", []).append({
        "at": _now_iso(),
        "action": "thaw",
        "feature_ids": [h.get("id") for h in hits],
        "locator": locator,
        "reason": reason.strip(),
    })
    _anchor_write(track_dir, data)
    out(dict(ok=True, thawed=[h.get("id") for h in hits], reason=reason.strip()))


def cmd_set_contract(track_dir, feature=None, locator=None, text=None):
    """Set a frozen feature's ``assertion_contract`` (the exogenous-judgment field).

    ``freeze`` captures the anchor's STRUCTURE (AC/TC/locators) but intentionally
    leaves ``assertion_contract`` blank — that field is human judgment: the
    semantic check the AC requires, the one thing the measured AC twin cannot
    see (it verifies a test is *named* right, not that it *asserts* the right
    thing). This command is the sanctioned way to fill it AFTER freezing,
    WITHOUT thawing the feature or tripping ``on-anchor-write-guard.py`` (which
    denies direct edits to ``feature-list.json``).

    Selection mirrors ``thaw``: ``--feature <F-AC-n>`` or ``--locator``.
    ``--text`` is required (use ``--text ""`` to deliberately clear a filled
    contract — recorded in audit, never silent). Refuses a thawed feature
    (already removed from the active anchor set) and a missing/unknown feature.
    """
    if text is None:
        out(dict(error="set-contract requires --text (use --text \"\" to clear)"))
        return
    data = _anchor_read(track_dir)
    if data is None:
        out(dict(error="no feature-list.json for this track — freeze first"))
        return

    hits = []
    for feat in data.get("features", []):
        if feat.get("thawed"):
            continue  # thawed = out of the active anchor set; contract is moot
        if feature and feat.get("id") == feature:
            hits.append(feat)
        elif locator and locator in (feat.get("test_locators") or []):
            hits.append(feat)
    if not hits:
        out(dict(error=f"no active frozen feature matches --feature/--locator "
                     f"({feature or locator}); thawed features are out of scope"))
        return

    for feat in hits:
        feat["assertion_contract"] = text  # may be "" (deliberate clear)
    data.setdefault("audit", []).append({
        "at": _now_iso(),
        "action": "set-contract",
        "feature_ids": [h.get("id") for h in hits],
        "locator": locator,
        "cleared": text == "",
        # Truncate the value in the audit trail so a wall of contract prose
        # doesn't bloat it; the live field holds the full text.
        "text_excerpt": (text.strip()[:120] + "…") if len(text.strip()) > 120 else text.strip(),
    })
    _anchor_write(track_dir, data)
    out(dict(
        ok=True,
        updated=[h.get("id") for h in hits],
        cleared=(text == ""),
        assertion_contract=text,
    ))


def cmd_anchor_status(track_dir, verify=False):
    """Read-only view of the frozen anchor — the counter-metric's data source.

    ``verify=True`` runs the Goodhart counter-metric (``compute_frozen_anchor_rate``
    with ``run=True``): executes the frozen subset and reports the pass/skip/
    drift rates alongside the structural counts. This is the command the
    ``--verify`` flag drives — the cheap structural view by default, the
    measured view on demand (running tests is not free).
    """
    data = _anchor_read(track_dir)
    if data is None:
        out(dict(ok=True, frozen=False, note="no feature-list.json for this track"))
        return
    features = data.get("features", [])
    active = [f for f in features if not f.get("thawed")]
    payload = dict(
        ok=True,
        frozen=True,
        track_id=data.get("track_id"),
        frozen_at=data.get("frozen_at"),
        frozen_from_spec_sha=data.get("frozen_from_spec_sha"),
        total_features=len(features),
        active_features=len(active),
        thawed=len(features) - len(active),
        grounded=sum(1 for f in active if f.get("test_locators")),
        audit_entries=len(data.get("audit", [])),
    )
    if verify:
        payload["anchor_rate"] = compute_frozen_anchor_rate(track_dir, run=True)
    out(payload)


def _track_id(track_dir):
    """Best-effort track_id from the state file (provenance only)."""
    try:
        from .core import load

        return load(track_dir).get("track_id")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Piece 3 — the Goodhart counter-metric: frozen_anchor_pass_rate
# ---------------------------------------------------------------------------
#
# The optimizing metric (``coverage_pct``) is self-reported and the measured AC
# twin (``ac_verification_measured_rate``) verifies a test is *named* right, not
# that it *passes*. This is its antagonistic pair (graph-engineering principle
# #1 — "metrics never travel alone"): it actually RUNS the frozen subset and
# records pass/fail independently of whatever the executor reported. If
# coverage climbs while ``frozen_anchor_pass_rate`` falls, that is the Goodhart
# alarm — the executor is gaming coverage at the cost of real behavior.
#
# Deliberately a SEPARATE function from ``compute_ac_integrity`` (not injected
# into it): the integrity rates run at planning time (new-track §2.3, before
# state exists) and in finalize hot paths, so they must stay fast and
# side-effect-free (read-only). Running tests is neither. The counter-metric is
# computed lazily by ``cmd_quality_snapshot`` and ``anchor-status --verify``,
# surfaced ALONGSIDE the AC rates — not merged into them.
#
# Conservative measurement contract (mirrors ``get_coverage_percent`` in
# ``on-batch-complete.py``): detect language via ``lib.coverage``, 20s timeout,
# never raises, returns ``None`` (unmeasured) on any ambiguity. Only
# Python/pytest gets precise per-node pass/fail today (``pytest path::test_fn``
# with ``-q``); other languages degrade to ``None`` rather than guess — same
# posture as the measured-twin degrading to ``None`` when naming is unadopted.

_ANCHOR_RUN_TIMEOUT = 20


def _active_frozen_locators(track_dir):
    """Return ``[locator]`` for every non-thawed frozen feature, flat.

    Locator forms (from ``freeze``): ``path::test_fn`` (grounded) — these are
    the only ones worth running. Ungrounded features (``test_locators == []``)
    contribute nothing measurable and are excluded from the denominator's
    ``runnable`` count (but counted in ``ungrounded``).
    """
    data = _anchor_read(track_dir)
    if data is None:
        return None, 0, 0
    locators = []
    ungrounded = 0
    for feat in data.get("features", []):
        if feat.get("thawed"):
            continue
        locs = feat.get("test_locators") or []
        if not locs:
            ungrounded += 1
            continue
        locators.extend(locs)
    return locators, len(locators), ungrounded


def _run_frozen_pytest(track_dir, locators):
    """Run the frozen subset under pytest, return ``{locator: "pass"|"fail"}``.

    Returns ``None`` (unmeasured) if pytest isn't the language, isn't
    installed, times out, or exits in a way we can't classify. Mirrors
    ``get_coverage_percent``'s never-raises contract. Classification is by
    pytest exit code: 0 = all passed; non-zero ≠ 5 = at least one failure
    (exit 5 = "no tests collected" → drift: a locator no longer resolves, not
    a failure — surfaced separately by the drift rate).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["pytest", "-q", "-p", "no:cacheprovider", "--no-header"] + list(locators),
            capture_output=True, text=True, cwd=str(track_dir),
            timeout=_ANCHOR_RUN_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return None
    except Exception:
        return None

    # Parse per-test pass/fail from the pytest short summary. ``-q`` emits a
    # final ``N passed`` / ``N failed, M passed`` line plus, on failure, a
    # ``FAILED path::test_fn - ...`` block. We classify by exit code + summary.
    verdicts = {loc: "unknown" for loc in locators}
    failed = set()
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        s = line.strip()
        if s.startswith("FAILED "):
            # ``FAILED tests/test_x.py::test_TC_1_1_a - reason``
            tgt = s[len("FAILED "):].split(" - ", 1)[0].strip()
            failed.add(tgt)
    rc = result.returncode
    if rc == 0:
        for loc in locators:
            verdicts[loc] = "pass"
    elif rc == 5:
        # No tests collected — every locator has drifted (no matching test fn).
        return None  # surfaced as drift via _locators_that_resolve, not failure
    else:
        for loc in locators:
            verdicts[loc] = "fail" if loc in failed else "pass"
    return verdicts


def compute_frozen_anchor_rate(track_dir, *, run=True):
    """The Goodhart counter-metric over the frozen anchor set (read-only).

    Returns a dict with:

    - ``frozen_anchor_pass_rate`` — % of runnable frozen locators that pass
      when run independently of the executor's self-report. ``None`` when
      unmeasured (no frozen list, no runnable locators, or the language isn't
      measurably supported).
    - ``frozen_anchor_skip_rate`` — % currently skipped (the silencing signal).
    - ``frozen_anchor_drift_rate`` — % whose ``path::test_fn`` no longer
      resolves to a real test function (the anchor has drifted from the tree).
    - ``runnable`` / ``ungrounded`` / ``drifted`` / ``passed`` / ``failed``
      / ``skipped`` — raw counts.
    - ``per_locator`` — ``{locator: "pass"|"fail"|"skipped"|"drifted"|...}``.

    ``run=False`` skips the subprocess (drift + skip only) — used when the
    caller wants the cheap structural view without executing tests.
    """
    locators, runnable, ungrounded = _active_frozen_locators(track_dir)
    if locators is None:
        return _rate_empty(reason="no_anchor")

    # Drift: does each locator's file+function still exist in the tree?
    drifted = []
    live = []
    for loc in locators:
        if _locator_resolves(track_dir, loc):
            live.append(loc)
        else:
            drifted.append(loc)
    drift_rate = round(100 * len(drifted) / runnable, 1) if runnable else None

    # Skip detection: scan the live test files for skip markers on the frozen
    # functions (the write-guard DENIES adding these, but a pre-existing skip
    # or one added outside the guard should still surface).
    skipped = _locators_currently_skipped(track_dir, live)
    skip_rate = round(100 * len(skipped) / runnable, 1) if runnable else None

    per_locator = {loc: ("drifted" if loc in drifted else "live") for loc in locators}
    for loc in skipped:
        per_locator[loc] = "skipped"

    passed = failed = 0
    pass_rate = None
    if run and live:
        verdicts = _run_frozen_pytest(track_dir, live)
        if verdicts is not None:
            for loc, v in verdicts.items():
                per_locator[loc] = v
                if v == "pass":
                    passed += 1
                elif v == "fail":
                    failed += 1
            decided = passed + failed
            pass_rate = round(100 * passed / decided, 1) if decided else None

    return {
        "frozen_anchor_pass_rate": pass_rate,
        "frozen_anchor_skip_rate": skip_rate,
        "frozen_anchor_drift_rate": drift_rate,
        "runnable": runnable,
        "ungrounded": ungrounded,
        "drifted": len(drifted),
        "skipped": len(skipped),
        "passed": passed,
        "failed": failed,
        "per_locator": per_locator,
        "reason": None,
    }


def _rate_empty(reason=None):
    return {
        "frozen_anchor_pass_rate": None,
        "frozen_anchor_skip_rate": None,
        "frozen_anchor_drift_rate": None,
        "runnable": 0,
        "ungrounded": 0,
        "drifted": 0,
        "skipped": 0,
        "passed": 0,
        "failed": 0,
        "per_locator": {},
        "reason": reason,
    }


def _locator_resolves(track_dir, locator):
    """Does ``path::test_fn`` still point at a real function in the tree?"""
    if "::" not in locator:
        return (Path(track_dir) / locator.split("::")[0]).exists()
    rel, _, fn = locator.partition("::")
    fn_name = fn.split("[")[0].strip()  # strip parametrize suffix
    f = Path(track_dir) / rel
    if not f.exists():
        return False
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    # Match a def line (python) — good enough for drift detection (the
    # write-guard already prevents silent deletion of frozen tests).
    import re

    return bool(re.search(rf"\bdef\s+{re.escape(fn_name)}\b", text))


def _locators_currently_skipped(track_dir, locators):
    """Return the locators whose frozen function currently carries a skip marker."""
    import re

    skipped = []
    # Group by file so each file is read once.
    by_file = {}
    for loc in locators:
        rel = loc.split("::", 1)[0]
        by_file.setdefault(rel, []).append(loc)
    for rel, locs in by_file.items():
        f = Path(track_dir) / rel
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for loc in locs:
            fn = loc.split("::", 1)[1].split("[")[0].strip()
            # A skip marker in the decorator region: the lines between this def
            # and the previous def (or start of file). That region holds the
            # decorator stack (@pytest.mark.skip / @Ignore) + the def line.
            m = re.search(rf"\bdef\s+{re.escape(fn)}\b", text)
            if not m:
                continue
            # Walk backwards from the def to the previous ``def `` or BOF,
            # collecting the decorator region (decorators + blank lines).
            region_start = text.rfind("\ndef ", 0, m.start())
            region_start = 0 if region_start == -1 else region_start + 1
            region = text[region_start : m.start()]
            if any(s in region for s in (
                "@pytest.mark.skip", "@pytest.mark.skipif", "pytest.skip(",
                "@Ignore", "@Disabled",
            )):
                skipped.append(loc)
    return skipped

