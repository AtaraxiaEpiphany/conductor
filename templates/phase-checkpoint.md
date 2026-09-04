# Phase Completion Verification & Checkpointing Protocol

**Trigger:** This protocol is executed immediately after a task is completed that also concludes a phase in `plan.md`.

> **Realization (plugin):** the L0 build verify (Step 2.5), the L1 verify (Step 3), and the AC-evidence-trace (Step 3.6) tiers are executed by three read-only verifier subagents fanned out **in parallel before** `conductor:phase-checker` — `conductor:build-runner` (runs the project's build/compile command once, no fix — the cheapest-first floor), `conductor:test-runner` (runs the test command once, no fix), and `conductor:ac-tracer` (runs `track-state spec-integrity`). `conductor:phase-checker` is the **synthesizer**: it consumes those verdicts as a graduated gate (build → test → AC trace), owns the Step 3 fix-and-retry (only when `test-runner` reports failure), then runs Steps 3.5 (L2) / 4–10. The `phase-checker` addenda are binding where they extend this template.

1.  **Announce Protocol Start:** Inform the user that the phase is complete and the verification and checkpointing protocol has begun.

2.  **Ensure Test Coverage for Phase Changes:**
    -   **Step 2.1: Determine Phase Scope:** To identify the files changed in this phase, you must first find the starting point. Read `plan.md` to find the Git commit SHA of the *previous* phase's checkpoint. If no previous checkpoint exists, the scope is all changes since the first commit.
    -   **Step 2.2: List Changed Files:** Execute `git diff --name-only <previous_checkpoint_sha> HEAD` to get a precise list of all files modified during this phase.
    -   **Step 2.3: Coverage scope:** which files owe a test is decided by the task-class gates — a file owes a test only if the task that produced it declares a class whose `gates` include coverage (the phase-checker's Step 2.2 addendum owns the rule) — never by a hardcoded extension list. Creating missing tests is owned by Step 3's fix-and-retry pass, which runs only when the test tier reports failure; a passing fleet already grounded the phase, so this step creates nothing on the happy path.

3.  **Execute Automated Tests with Proactive Debugging:**
    -   Before execution, you **must** announce the exact shell command you will use to run the tests.
    -   **Example Announcement:** "I will now run the automated test suite to verify the phase. **Command:** `CI=true npm test`"
    -   Execute the announced command.
    -   If tests fail, you **must** inform the user and begin debugging. You may attempt to propose a fix a **maximum of two times**. If the tests still fail after your second proposed fix, you **must stop**, report the persistent failure, and ask the user for guidance.

3.6.  **AC Evidence Trace (spec-bearing tracks):**
    -   **Decide applicability:** if `{TRACK_DIR}/spec.md` does not exist, or has no `## Acceptance Criteria` section, skip this step (record `AC_TRACE: skipped (no spec/ACs)`) and proceed to Step 4 — tracks without a formal spec are not penalized.
    -   Run `track-state spec-integrity "{TRACK_DIR}"` and parse the JSON.
    -   **Gate verdict:** if `ac_integrity_gate` is `FAILED` → **STOP**. Paste the gate string verbatim as the failure reason — it names the offending AC IDs and the exact authoring fix (e.g. "add a `TC-{n}.{m} | AC-{n}` row under ## Test Scenarios", "annotate the implementing task in plan.md with a `<!-- AC-n -->`"). This is a **spec/plan authoring defect, not a code defect** — fix `spec.md` / `plan.md`, then re-run the phase; do not retry the implementing task.
    -   **Evidence grounding:** from the `ac_evidence` list, record the per-AC grounding summary into the Step 7 verification report — TCs `measured` (grounded by a real `def test_TC_{n}_{m}_*`) vs `claimed` (in `evidence.tc_coverage` but no named test) vs `missing`. By default ungrounded TCs are advisory (`AC_TRACE: warn (N ungrounded)`); under `CONDUCTOR_AC_VERIFY_STRICT=1`, any ungrounded TC fails the checkpoint. `AC_TRACE: passed` when every AC's TCs are grounded.

4. Propose a Detailed, Actionable Manual Verification Plan:
    -   **CRITICAL:** To generate the plan, first analyze `product.md`, `product-guidelines.md`, and `plan.md` to determine the user-facing goals of the completed phase.
    -   You **must** generate a step-by-step plan that walks the user through the verification process, including any necessary commands and specific, expected outcomes.
    -   The plan you present to the user **must** follow this format:

        **For a Frontend Change:**
        ```
        The automated tests have passed. For manual verification, please follow these steps:

        **Manual Verification Steps:**
        1.  **Start the development server with the command:** `npm run dev`
        2.  **Open your browser to:** `http://localhost:3000`
        3.  **Confirm that you see:** The new user profile page, with the user's name and email displayed correctly.
        ```

        **For a Backend Change:**
        ```
        The automated tests have passed. For manual verification, please follow these steps:

        **Manual Verification Steps:**
        1.  **Ensure the server is running.**
        2.  **Execute the following command in your terminal:** `curl -X POST http://localhost:8080/api/v1/users -d '{"name": "test"}'`
        3.  **Confirm that you receive:** A response with a status of `201 Created`.
        ```

5.  **Await Explicit User Feedback:**
    -   After presenting the plan, ask the user for confirmation: "**Does this meet your expectations? Confirm with yes or provide feedback.**"
    -   **PAUSE** and await the user's response. Do not proceed without an confirmation.

6.  **Create Checkpoint Commit:**
    -   Stage all changes. If no changes occurred in this step, proceed with an empty commit.
    -   **Orchestrator bookkeeping:** a modified `track-state.json` / `plan.md` you did NOT edit is conductor auto-bookkeeping — the phase-boundary auto-fixes (phase-status propagation, index syncs), surfaced as `fixes_applied` on the verdict envelope together with a `bookkeeping` commit line. Stage it with this checkpoint commit. NEVER restore/revert it — reverting un-completes the phase and desyncs state from plan.md.
    -   Perform the commit with a clear and concise message (e.g., `chore(conductor): Checkpoint end of Phase X`).

7.  **Attach Auditable Verification Report using Git Notes:**
    -   **Step 7.1: Draft Note Content:** Create a detailed verification report including the automated test command, the manual verification steps, and the user's confirmation.
    -   **Step 7.2: Attach Note:** Use the `git notes` command and the full commit hash from the previous step to attach the full report to the checkpoint commit.

8.  **Get and Record Phase Checkpoint SHA:**
    -   **Step 8.1: Get Commit Hash:** Obtain the short hash of the *just-created checkpoint commit* (`git log -1 --format="%h"` — use exactly what git prints; large repositories extend past 7 characters).
    -   **Step 8.2: Update Plan:** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track-state" add-checkpoint {TRACK_DIR} {PHASE_INDEX} {sha}` to update the phase heading in `plan.md` with `[checkpoint: <sha>]`.
    -   **Step 8.3: Verify:** Confirm the command output contains `ok: true`. An `error` JSON is a real gate — the command verifies the sha resolves to a commit in this repo; re-run `git log -1 --format="%h"` and retry once with the fresh value. NEVER hand-edit `[checkpoint: ...]` into plan.md to bypass the gate.

9.  **Commit Plan Update:**
    - **Action:** Stage the modified `plan.md` file.
    - **Action:** Commit this change with a descriptive message following the format `chore(conductor): Mark phase '<PHASE NAME>' as complete`.

10.  **Announce Completion:** Inform the user that the phase is complete and the checkpoint has been created, with the detailed verification report attached as a git note.
