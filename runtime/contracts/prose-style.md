---
type: concept
sources:
  - scripts/lint-prose-impl-leak.py
last_verified: 2026-08-04
---

# Prompt Prose Style

Authoring conventions for the **prompt surface** — the markdown that gets injected
into LLM context (`agents/`, `skills/`, `runtime/`) or is read by maintainers
(`conductor/`, top-level `README.md`). Every word here is paid for twice: once as
a token in the acting model's context, once as attention it crowds out. The fix
is not to write less, but to carry **semantics** (what + why) and drop
**mechanism** (which file / which tool) — unless the mechanism *is* the instruction.

This is the sibling of [[runtime/contracts/doc-conventions]], which governs the
**corpus docs** (`conductor/` wiki/spec tree). That page is for what
`corpus-writer` / `wiki-synthesizer` emit; this page is for the prompts we
hand-author.

## The core principle

> Carry semantics (what + why), not mechanism (which file / which tool) —
> unless the mechanism IS the instruction.

## The decision test

Before writing a `.py` filename or a tool name into prose, ask:

> **Does the reader act on this exact token?**
>
> - **Yes** — the agent must type this command, or the harness matches this token → keep.
> - **No** — the reader would do the right thing without it → cut. It is noise, and it will rot.

## Taxonomy

| Bucket | Shape | Verdict | Why |
|---|---|---|---|
| **A — Invocation ref** | The exact script / CLI the agent must run (`track-state registry-doc`, `coverage-pct.py`) | **KEEP** | The agent must type this exact string; dropping it breaks the call. |
| **B — Single-source pointer** | "The rule lives here; don't restate it" (`lib/frontmatter.py` is the one parser; the drift-killer lint enforces the no-second-home rule) | **KEEP** | Prevents a second, drift-prone home for the same invariant. |
| **C — Line-number citation** | `path/to/file.py:NN` in prose | **CUT** | Guaranteed drift — insert one line above and it silently points at the wrong code. Cite the stable symbol instead (e.g. `_git_commit` in `git_ops.py`). **Lint-enforced.** |
| **D — Tool-as-verb** | "Use the Write tool to write `spec.md`" | **CUT** | The verb "write `spec.md`" already implies the action; the harness picks the tool. Naming it is redundant mechanism. |
| **E — Tool-as-constraint** | "you have no Write tool"; "the `Agent` tool is fenced to two dispatch kinds" | **KEEP** | Names the exact token a hook matches (`PreToolUse:Agent` fires on the literal `Agent`). The mechanism *is* the constraint. |
| **F — Maintainer-rationale** | "(`tests/test_x_wiring.py` pins this)"; "This is the historical behavior;" | **CUT from agent prompts** | The acting LLM cannot act on a test-file reference or a history aside — that content is for whoever edits the prompt. Tolerable in `conductor/design/`. |

## Altitude by document type

Trim more aggressively the closer prose sits to the acting model:

- **Agent prompts (`agents/`)** — keep only **A** (exact commands) and **E** (hook-matched constraints). Cut **D** and **F** ruthlessly; they cost tokens on every dispatch.
- **Skills (`skills/`)** — same, plus minimal **B** pointers where a step name alone would leave the model guessing.
- **Contracts (`runtime/contracts/`)** — keep **A** and **B**; cut **C** (convert any line ref to a symbol). **D**/**F** are tolerable here since these are read by humans and loaded on demand, not resident.
- **Design docs (`conductor/design/`)** — the freest altitude; rationale and history are the point. Still no line numbers (**C**).

## Author checklist

Before adding a `.py` filename or a tool name to a prompt:

1. **Must the agent type this exact command?** → yes = **A**, keep.
2. **Am I pointing at the single source of a rule** so it isn't restated here? → yes = **B**, keep.
3. **Am I citing a line number?** → never. Cite the symbol (**C**).
4. **Is the tool name a constraint a hook enforces?** → yes = **E**, keep.
5. **Am I just naming the tool the verb already implies?** → drop it (**D**).
6. **Is this a parenthetical for a future maintainer, not the acting model?** → cut from prompts (**F**).

## Mechanical enforcement

Bucket **C** (line-number citations) is enforced by `scripts/lint-prose-impl-leak.py`,
wired through `tests/test_prose_impl_leak.py` — it scans the prompt surface and
fails on any `file.<ext>:NN` outside a fenced block. The other buckets are
judgment calls a regex gets wrong (a tool name is sometimes **D**, sometimes
**E**), so they live here as review guidance, not a gate.

## See Also

- [[runtime/contracts/doc-conventions]] — the sibling contract, for corpus-doc authoring.
- [[runtime/contracts/grill-discipline]] — the one-home grill procedure; a consumer that Read-on-demand follows rather than restates (Bucket B in practice).
- [[runtime/core-contract]] — behavioral invariants; resident in every session.
