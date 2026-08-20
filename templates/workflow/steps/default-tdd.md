# Default TDD Workflow (Steps 3-8)

The executor-owned steps of the Standard Task Workflow — Red → Green →
Refactor → Coverage → Deviations → Commit. This is the docfile every task
runs whose leading tag declares no `workflow_doc` in the task-type registry
(resolve a tag's docfile via `track-state registry-doc --tag <Tag>`).
Orchestrator-owned Steps 1-2/9-11 stay in
`${CLAUDE_PLUGIN_ROOT}/templates/task-workflow.md`. A project may override
this file at `conductor/workflow/steps/default-tdd.md` (project wins).

3. **Write Failing Tests (Red)** – create test file in the project's designated test directory (typically `tests/`), following the naming and placement conventions in the loaded code styleguide. Run it, **confirm failure**; show the failing output.
4. **Implement to Pass Tests (Green)** – write **minimal** code to make the tests pass; confirm pass.
5. **Refactor** – one bounded, **diff-scoped** pass on the code you just wrote, under your passing Step-3 tests (run the project lint/format on your changed files, fix findings). **behavior-preserving** — public-API/behavior changes are Step 7, not refactor. Commit as `refactor(area): …`; Step 6's coverage run is your **green-confirm** (revert on red, never fix forward). Skip if it'd trip the §7.0 tripwire. *(executor §4.0 binds it; exempt when the task's resolved registry profile is `tdd_exempt` — a registry-derived set, not a tag list; resolve via the injected `[Conductor Registry]` block or `track-state registry-doc`.)*
6. **Verify Coverage** – run coverage tool, **must be >80%**. If not, add tests until the threshold is met. **Do not commit if coverage is below 80%**.
7. **Document Deviations** – if implementation diverges from the tech stack, **stop**, update `tech-stack.md` with the change and rationale, then resume.
8. **Commit Code Changes** – stage all code changes and commit with a conventional message (e.g., `feat(ui): ...`).
