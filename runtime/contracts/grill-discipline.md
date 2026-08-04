---
type: concept
sources:
  - skills/brief
last_verified: 2026-08-04
---

# Grill Discipline

The canonical procedure for grilling a user into a shared understanding before a
spec-grade artifact is written — a `brief.md`, a discovery proposal, any document
whose errors propagate downstream into `spec.md` / `plan.md`. **One home for the
discipline:** surfaces that grill Read this contract on demand and follow it, rather
than restating the rules (see [[runtime/contracts/prose-style]] Bucket B — a second
restated home silently drifts, and a grill is exactly the kind of load-bearing
procedure that drifts badly).

**Loaded on demand by:** `skills/brief` (the grill before `spec.md`). Future grill
surfaces adopt the same pointer. **Not loaded by:** executors that receive
already-resolved answers (`spec-planner` takes `USER_ANSWERS`), config writers that
batch-confirm (`strategy-writer` §4), or read-only auditors that hold the
four-quadrant stance as a *lens*, not a grill (`spec-reviewer`) — those pick a
lighter posture (§1) and skip the grill loop (§3).

## 1. The posture spectrum (choose your interaction posture FIRST)

Grilling is a high-turn-cost discipline justified only when the artifact is
spec-grade input and an error propagates. Most surfaces want a lighter posture.
Before anything else, place your surface on this spectrum:

| Posture | When it applies | Discriminator |
|---|---|---|
| **Full grill** (this contract) | high-stakes *spec input* — a brief, a discovery proposal | an uncaught wrong premise or unknown propagates into `spec.md` / `plan.md` and costs a re-plan |
| **Batch-confirm** | low-stakes, easily edited — config docs, strategy | a wrong call is one edit to fix; interrogating costs more attention than the mistake would |
| **Ask-nothing** | execution — an executor given resolved answers | the answers are already in `USER_ANSWERS` / `brief.md`; asking re-litigates settled ground |
| **Four-quadrant as a lens** | read-only audit — `spec-reviewer` | the stance (§2) sharpens the review; no turns, no questions, findings only |

The one-line rule: **grill for spec input; batch-confirm for config; ask-nothing for
execution; four-quadrant as a lens for audits.** If you are not producing spec-grade
input under propagation risk, do not run the grill loop — use a lighter posture.
Over-grilling is a tax the user pays in attention every single turn.

## 2. The four-quadrant stance (how to think, not what to ask)

A spec-input grill is a two-party epistemic act — you and the user each know things
the other doesn't, and each kind of gap closes differently. Hold this 2×2
(you × user × known × unknown) as your posture before the grill. Each quadrant
points at the mechanism below that implements it:

1. **SHARED-KNOWN** — goals / context / boundaries already in the request, the
   discovered docs, and `product.md` / `purpose.md` / `tech-stack.md`. **Do not
   re-ask.** A fact you can read is yours to gather, never a question (§3 rule 2 —
   look-it-up-first). The decisions are the user's; the facts are yours.
2. **YOUR-KNOWN / USER-UNKNOWN** — context only in the user's head (the real
   motivation, the unspoken deadline, the stakeholder who must sign off). Surface at
   most ONE such question per grill node; if you can state a defensible assumption,
   state it and ask the user to confirm (recommended-answer-first, §3 rule 3).
3. **YOUR-UNKNOWN / USER-KNOWN** — knowledge, risks, or better paths the user may
   NOT have considered, because you read the codebase and they didn't (yet). If a
   stated goal, an out-of-scope, or a constraint looks **wrong** — solving the wrong
   problem, over-constraining, or mistaking a symptom for a cause — say so directly
   and propose the alternative with trade-offs (the premise-challenge pass, §4).
4. **SHARED-UNKNOWN** — unknowns NEITHER party settles by reading (a behavior under
   load, a third-party API's real limits, a migration's blast radius). Don't just
   confess these under Open Questions — convert each into a testable hypothesis
   (operationalize, §5).

Quadrants 1 and 2 are baseline competence (capture the known). Quadrants 3 and 4 are
where an expert collaborator earns its keep (challenge the wrong, operationalize the
unknown). A grill that only transcribes what the user already knows wastes the
asymmetric knowledge.

## 3. The grill loop (one decision per iteration)

> **MUST — one question at a time, via `AskUserQuestion`, no exceptions.**
> Every decision is posed as a **single** `AskUserQuestion` call, and you **wait
> for the answer before posing the next one.** Never batch two decisions into one
> prompt; never free-text a question as plain prose instead of calling the tool.
> Asking several at once is bewildering — the user can't give each decision the
> thought it deserves.

Walk the grill's decision tree (the surface owns its own tree — `brief` owns the
brief-section tree) resolving dependencies one-by-one so each answer informs the
next:

1. **Pick the next decision** — the first not-yet-resolved node in dependency order.
   Don't jump ahead; later questions depend on earlier ones.
2. **Look it up before you ask.** If a fact can be found by exploring the
   environment — discovered docs, `product.md`, `tech-stack.md`, the codebase,
   `purpose.md` — look it up rather than asking. The *decisions* are the user's; the
   *facts* are yours to gather. Never ask a question you could answer by reading.
3. **Pose ONE question** via `AskUserQuestion`, and **provide your recommended answer
   as the first option** (marked "(Recommended)") with a one-line rationale grounded
   in what you read. The user confirms, corrects, or picks "Other." A grilling
   without recommendations is just an interrogation — you are an expert collaborator,
   not a stenographer.
4. **Record the answer**, note any new dependency it opens (an Out-of-Scope decision
   may raise a fresh Open Question), and loop to step 1.

**Informed, not generic.** Surface what you observed ("Found `X` — recommend treating
its boundary as out-of-scope. Confirm?") and let the user confirm or correct. Informed
questions with a recommended answer beat generic ones. **Skip a branch only when it's
genuinely resolved** — if the request plus discovered docs already fully answer a
decision, state your understanding and ask the user to confirm that one point rather
than re-asking from scratch. A confirmation prompt is the floor, not a silent default.

## 4. Premise-challenge pass (Q3 — do this FIRST, once)

Before the convergent grill, state your read of the artifact's **central premise** in
one sentence (the problem this work believes it's solving). Then ask: is any premise
**questionable** — a goal that may solve the wrong problem, an out-of-scope that may
over-constrain (ruling out a cheaper path the user didn't see), a constraint that may
be a *symptom* of a deeper cause rather than a real limit?

If so, pose **at most one** challenge via `AskUserQuestion` (recommended option = your
better alternative, with the trade-off named; "Other" lets the user defend the
original). Bounded to **one challenge total** so the grill stays tight — pick the
single highest-leverage disagreement. If the premise holds, proceed straight to the
grill loop.

This is quadrant 3: your asymmetric knowledge (codebase, docs, prior tracks) is wasted
if you only transcribe what the user already knows. In `brief`, `## Out of Scope` is
copied **verbatim** into the spec — a wrong premise propagates unchanged, so a
one-question catch at the grill is far cheaper than a spec re-plan later.

## 5. Operationalize unknowns (Q4)

When an unknown is decidable by an experiment (quadrant 4 — a shared-unknown neither
party settles by reading), don't just confess "we don't know" under Open Questions.
Convert it into a testable hypothesis. For each such unknown, capture:

- **The hypothesis** — what specifically we'd need to learn (a falsifiable claim, not
  an open-ended "figure out X").
- **The minimal experiment** — the smallest action that surfaces the answer (a spike,
  a probe, a one-file repro), not a full implementation.
- **The single variable** — the one thing that differs between the options (if you
  can't name one, the experiment isn't tight enough).
- **The success/fail signal** — the concrete data that settles it (a timing number, an
  error gone, a count), so planning can act on the result.

An artifact that names its falsifiable predictions can correct itself during planning;
one that only confesses unknowns hands them forward unchanged. Unknowns that are
genuinely "ask the stakeholder" (a human decision, not an experiment) stay as plain
open questions — don't fabricate an experiment where the real resolution is a person.

## 6. Signal grill-done before writing

The grill ends only when every decision-tree node is resolved-or-confirmed. Then —
and only then — write the artifact. An artifact written from guesses is worse than
none.

Where a deterministic enforcer gates the write (the `brief` grill is gated by the
`on-brief-grill-tripwire` hook), the **done-signal** is the real gate — NOT a raw
question count. The count is only a backstop, and it's a *proxy* that's wrong exactly
when the grill is done well: many decisions are pre-resolved by reading (§3 rule 2),
so a grill completed in fewer than the floor count of questions is legitimate.
Signaling explicitly is how you tell the gate *"the grill is genuinely complete; the
low count is skillful look-it-up-first, not a shortcut."* Without it, a well-done
low-count grill is wrongly blocked. Always emit the surface's done-signal after the
last question, before the Write.

## See Also

- [[runtime/contracts/prose-style]] — Bucket B: why this discipline has one home and must not be restated elsewhere.
- [[runtime/core-contract]] — behavioral invariants; resident in every session.
