# Testing Strategy

## Test Directory Structure

<!-- DYNAMIC: This section is generated per-project during setup. -->
<!-- The test_root is determined by scanning the project for existing test directories. -->

### test_root: `{TEST_ROOT}`

All test files MUST be created under `{TEST_ROOT}`. NEVER co-locate test files with source code (exception: Go `_test.go` files follow language convention).

## File Placement Policy

### By Language

| Language | Test Directory | File Pattern | Class/Function Pattern | Convention |
|----------|---------------|--------------|----------------------|------------|
| Python | `tests/` | `test_{module}.py` | `Test{Class}` / `test_{scenario}_{outcome}` | Mirror `src/` structure | <!-- lang:python -->
| JavaScript | `__tests__/` or `tests/` | `{module}.test.js` | `describe` + `test`/`it` | Mirror `src/` structure | <!-- lang:javascript -->
| TypeScript | `__tests__/` or `tests/` | `{module}.test.ts` | `describe` + `test`/`it` | Mirror `src/` structure | <!-- lang:typescript -->
| Go | Same package | `{name}_test.go` | `Test{Function}` | Go convention: co-located | <!-- lang:go -->
| C++ | `tests/` | `{module}_test.cc` | `TEST_F({Class}Test, {Scenario})` | Mirror `src/` structure | <!-- lang:cpp -->
| C# | `{Project}.Tests/` | `{Class}Tests.cs` | `[Fact] {Method}_{Scenario}_{Outcome}()` | Mirror project structure | <!-- lang:csharp -->
| Java | `src/test/java/` | `{Name}Test.java` | `@Test void {method}_{scenario}_{outcome}()` | Mirror `src/main/java/` package structure | <!-- lang:java -->
| Dart | `test/` | `{name}_test.dart` | `group` + `test` | Mirror `lib/` structure | <!-- lang:dart -->

### Mirror Rule

Source-to-test path mapping follows a strict mirror pattern:

```
src/{package}/{module}/{file}  →  {TEST_ROOT}/{module}/{test_file}
```

Examples:
- `src/myapp/services/user.py` → `tests/services/test_user.py` <!-- lang:python -->
- `src/components/Button.tsx` → `__tests__/components/Button.test.tsx` <!-- lang:typescript -->
- `lib/src/models/user.dart` → `test/models/user_test.dart` <!-- lang:dart -->
- `MyApp/Services/UserService.cs` → `MyApp.Tests/Services/UserServiceTests.cs` <!-- lang:csharp -->
- `src/main/java/com/acme/user/UserService.java` → `src/test/java/com/acme/user/UserServiceTest.java` <!-- lang:java -->

### Existing Convention Rule

Before creating any test file:
1. Scan `{TEST_ROOT}/` for existing test files.
2. If files exist, follow the established naming and placement convention.
3. If no files exist, use the language's default pattern from the table above.

### Scripts & Dev Commands

Project scripts (run/build helpers, `start.sh`, `seed.sql`, one-off CLIs) belong at the **project root** in conventional locations — **not** under `conductor/`. The `conductor/` tree is the spec/wiki/planning map that Conductor agents route on; it is not a home for executable code.

| File type | Location |
|-----------|----------|
| Dev/run scripts (`start.sh`, `run.sh`) | `./scripts/` at project root, or a `Makefile` / `package.json` script |
| Test scripts | `{TEST_ROOT}/` (above) |
| One-off CLIs / bins | `./bin/` |

Rationale: tooling (`pytest`, `make`, CI) already looks in these places, and keeping executable code out of `conductor/` preserves the doc map's integrity — a `scripts/` row in `conductor/index.md` would be non-documentation noise that no agent routes on.

## Test Types

| Type | Directory | Purpose | Scope |
|------|-----------|---------|-------|
| Unit | `{TEST_ROOT}/{module}/` | Single function/class in isolation | Fast, no external dependencies |
| Integration | `{TEST_ROOT}/integration/` | Multi-component interactions | May use real DB/API |
| E2E | `{TEST_ROOT}/e2e/` | Full user flows | Slow, production-like |

## Coverage

- **Threshold:** >80% for all new code.
- **Scope:** Line coverage for unit tests. Branch coverage encouraged.
- **Exclusions:** Generated code, type definitions, pure configuration.
- **Enforcement:** Coverage gate (Firewall F3). No commit if below threshold.

## Shared Test Infrastructure

| Artifact | Location | Purpose |
|----------|----------|---------|
| Global fixtures | `{TEST_ROOT}/conftest.py` or `{TEST_ROOT}/setup.ts` | Shared setup/teardown |
| Module fixtures | `{TEST_ROOT}/{module}/conftest.py` | Module-scoped fixtures |
| Test helpers | `{TEST_ROOT}/helpers/` | Reusable utilities |
| Mocks / Fixtures | `{TEST_ROOT}/fixtures/` or `{TEST_ROOT}/__mocks__/` | Test data and mock implementations |
| Factories | `{TEST_ROOT}/factories/` | Test data generation |

## Cache & Artifact Management

<!-- DYNAMIC: Injected from dev-commands/<lang>.md Environment section during setup. -->

Keep test artifacts out of the source tree:
- Python: `PYTHONPYCACHEPREFIX` redirects `__pycache__/` to `/tmp/`. <!-- lang:python -->
- Go: `GOCACHE` redirects build cache to `/tmp/`. <!-- lang:go -->
- C++: Build artifacts stay in `build/` (out-of-source CMake). <!-- lang:cpp -->
- C#: `bin/` and `obj/` stay in per-project directories (gitignored). <!-- lang:csharp -->
- Java: `target/` (Maven) and `build/` (Gradle) gitignored. <!-- lang:java -->
- JS/TS: `coverage/` and `.nyc_output/` gitignored. <!-- lang:javascript typescript -->
- Dart: `.dart_tool/` and `build/` gitignored. <!-- lang:dart -->

## Violation Recovery

If a test file is found outside `{TEST_ROOT}/`:
1. Move it to the correct location per the mirror rule.
2. Update all imports.
3. Run tests to verify nothing broke.
4. Commit with `refactor(test): move misplaced test file`.
