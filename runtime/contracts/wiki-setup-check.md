---
type: concept
sources:
  - skills/wiki
  - skills/wiki-doctor
last_verified: 2026-06-26
---

# Wiki Setup Check

Shared setup protocol for the `wiki` and `wiki-doctor` skills. Both verify the
Conductor wiki infrastructure exists before proceeding; the block was
near-verbatim across the two skills (differing only in whether `purpose.md` is
listed).

## Protocol

1. **Locate Wiki Files:** Resolve via project CLAUDE.md TOC or default paths:
   - `conductor/overview.md` — Wiki overview (regenerated after each track)
   - `conductor/log.md` — Append-only chronological record
   - `conductor/index.md` — Central navigation hub
2. **Verify Existence:** Check each file exists using Glob.
3. **Handle Failure:** If `conductor/overview.md` or `conductor/log.md` is missing → halt: "Wiki infrastructure incomplete — missing: `<files>`. Run `/conductor:setup` to initialize."

## Caller-specific extras

- `wiki` additionally resolves `conductor/purpose.md` (directional intent — read/co-edited by the `purpose` sub-command). `wiki-doctor` does not require it.

## See Also

- [[runtime/contracts/doc-conventions]] — corpus authoring conventions.
