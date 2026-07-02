---
type: concept
sources:
  - skills/wiki
  - skills/wiki-doctor
last_verified: 2026-06-26
---

# Agent Error Handling (wiki skills)

Shared error-handling protocol for the `wiki` and `wiki-doctor` skills. Each
skill fetches this on demand and substitutes the **relevant agent** and
**result-block delimiter** for the path it is currently on (see the reference
table below). Inlining this in every skill duplicated the block and caused
copy-paste drift (one skill naming an agent it never dispatches).

## 1. Infrastructure Missing

If `conductor/overview.md` or `conductor/log.md` does not exist:

→ HALT: "Wiki infrastructure missing: `<files>`. Run `/conductor:setup` to initialize."

## 2. Tool Call Failure

If any Read/Grep/Glob/Agent/Write/Edit tool call fails:

→ STOP → announce: "Wiki tool failure: `<tool>` failed with: `<error>`." → await instructions.

## 3. Agent Failure

If the dispatched agent returns `STATUS: FAILURE`:

→ Announce: "`<agent>` failed: `<reason>`." → await instructions.

## 4. No Result Block

If the agent completes but no `<RESULT-BLOCK>` delimiter is detected:

→ Announce: "`<agent>` completed without structured result. Check the conversation for details."

## Agent / delimiter reference

| Caller | Agent | Result-block delimiter |
|---|---|---|
| `wiki` query (§4) | `conductor:wiki-researcher` | `---WIKI RESEARCH RESULT---` |
| `wiki` ingest (§6) | `conductor:corpus-writer` → `conductor:wiki-synthesizer` | `---DOC SYNC RESULT---` |
| `wiki-doctor` lint (§3) | `conductor:doc-linter` | `---DOC LINT RESULT---` |
| `wiki-doctor` diff (§4) | `conductor:wiki-differ` | `---WIKI DIFF RESULT---` |

## See Also

- [[conductor/design/doc-conventions]] — corpus authoring conventions.
