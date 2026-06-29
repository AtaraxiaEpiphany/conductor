"""Parse spec.md into an FR/NFR/AC/TC inventory.

spec.md grammar (templates/spec-scaffold.md):

    ## Requirements
    ### Functional Requirements
    - FR-1: [requirement] [(ref)](path/to/doc)
    ### Non-Functional Requirements
    - NFR-1: [requirement]
    ## Acceptance Criteria
    - AC-1: [measurable criterion]
    ## Test Scenarios
    | ID     | AC Ref | Scenario | Expected Outcome |
    | TC-1.1 | AC-1   | ...      | ...              |

IDs are collected **section-scoped** — an ``AC-1``/``FR-1`` mention in prose
(e.g. the References or Constraints section) is NOT captured, only bullets/rows
inside their owning heading. Mirrors plan_parse.parse_plan's line-walk +
``errors``/``warnings`` shape so the two parsers read consistently.
"""
import re
from pathlib import Path

# Markdown heading: group(1)=_hashes, group(2)=title text.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

# Requirement/criterion bullets: ``- FR-1: ...``. group(1)=number; group(2)=the
# body text after the ID (consumed by the EARS lint in spec_integrity). group(1)
# is unchanged, so the frs/nfrs ID lists stay byte-identical. The separator class
# eats the ``:``/space/dash between ID and body so it isn't carried into the text.
_FR = re.compile(r"^-\s+FR-(\d+)\b[\s:\-]*([^\n]*)")
_NFR = re.compile(r"^-\s+NFR-(\d+)\b[\s:\-]*([^\n]*)")
_AC = re.compile(r"^-\s+AC-(\d+)\b")

# Test-scenario table row: ``| TC-1.1 | AC-1 | ... |``. group(1)/group(2)=TC
# numbers, group(3)=the AC number it traces to.
_TC_ROW = re.compile(r"^\|\s*TC-(\d+)\.(\d+)\s*\|\s*AC-(\d+)\s*\|")

# The four headings that own collectable IDs. Anything else ends the section.
_SECTION_HEADINGS = {
    "functional requirements": "fr",
    "non-functional requirements": "nfr",
    "acceptance criteria": "ac",
    "test scenarios": "tc",
}


def parse_spec(spec_path):
    """Parse spec.md → inventory dict.

    Returns::

        {"frs": ["FR-1", ...], "nfrs": [...], "acs": [...],
         "fr_items": [{"id": "FR-1", "text": "<body>"}, ...],
         "nfr_items": [{"id": "NFR-1", "text": "<body>"}, ...],
         "tcs": [{"id": "TC-1.1", "ac": "AC-1"}, ...],
         "tc_to_ac": {"TC-1.1": "AC-1", ...},
         "errors": [...], "warnings": [...]}

    IDs are returned in document order (duplicates not deduped — callers that
    need a universe normalize via ``set``). ``errors`` block nothing here (the
    parser is best-effort); ``warnings`` are advisory.
    """
    errors = []
    warnings = []
    frs, nfrs, acs = [], [], []
    fr_items, nfr_items = [], []
    tcs = []
    tc_to_ac = {}

    text = Path(spec_path).read_text()
    section = None  # one of "fr" / "nfr" / "ac" / "tc" / None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        hm = _HEADING.match(line)
        if hm:
            title = hm.group(2).strip().lower()
            section = _SECTION_HEADINGS.get(title)
            continue

        if section == "fr":
            m = _FR.match(line.lstrip())
            if m:
                rid = f"FR-{m.group(1)}"
                frs.append(rid)
                fr_items.append({"id": rid, "text": m.group(2).strip()})
        elif section == "nfr":
            m = _NFR.match(line.lstrip())
            if m:
                rid = f"NFR-{m.group(1)}"
                nfrs.append(rid)
                nfr_items.append({"id": rid, "text": m.group(2).strip()})
        elif section == "ac":
            m = _AC.match(line.lstrip())
            if m:
                acs.append(f"AC-{m.group(1)}")
        elif section == "tc":
            m = _TC_ROW.match(line)
            if m:
                tc_id = f"TC-{m.group(1)}.{m.group(2)}"
                ac_ref = f"AC-{m.group(3)}"
                tcs.append({"id": tc_id, "ac": ac_ref})
                tc_to_ac[tc_id] = ac_ref

    if not (frs or nfrs or acs):
        warnings.append("spec.md has no FR/NFR/AC entries")

    return {
        "frs": frs,
        "nfrs": nfrs,
        "acs": acs,
        "fr_items": fr_items,
        "nfr_items": nfr_items,
        "tcs": tcs,
        "tc_to_ac": tc_to_ac,
        "errors": errors,
        "warnings": warnings,
    }
