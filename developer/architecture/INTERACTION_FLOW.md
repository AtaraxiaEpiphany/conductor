---
title: Interaction Flow
audience: developer
status: stable
last_updated: 2026-05-11
related:
  - overview.md
  - INTERACTION_MECHANISM.md
  - state-model.md
---

# Conductor Interaction Flow Diagrams

## Master Flow: From User Command to Task Completion

```mermaid
flowchart TD
    Start([User Command]) --> Skill{Skill?}

    Skill -->|setup| SetupFlow[Setup Flow]
    Skill -->|new-track| NewTrackFlow[New Track Flow]
    Skill -->|implement| ImplementFlow[Implement Flow]
    Skill -->|status| StatusFlow[Status Flow]
    Skill -->|review| ReviewFlow[Review Flow]
    Skill -->|revert| RevertFlow[Revert Flow]

    SetupFlow --> SetupSA[Dispatch: project-analyzer]
    SetupSA --> SetupSA2[Dispatch: spec-planner]
    SetupSA2 --> SetupSA3[Dispatch: spec-reviewer]
    SetupSA3 --> SetupInit[track-state init]
    SetupInit --> SetupDone([Setup Complete])

    NewTrackFlow --> NewTrackSA[Dispatch: spec-planner]
    NewTrackSA --> NewTrackSA2[Dispatch: spec-reviewer]
    NewTrackSA2 --> NewTrackInit[track-state init --execution-mode]
    NewTrackInit --> NewTrackDone([Track Created])

    ImplementFlow --> ImplRecover[track-state recover]
    ImplRecover --> ImplNext{Next Action?}

    ImplNext -->|dispatch_explorer| ImplExplorer[Dispatch: explorer]
    ImplExplorer --> ImplExploreResult[Process: exploration.md]
    ImplExploreResult --> ImplPhase

    ImplNext -->|dispatch_executor| ImplExecutor[Dispatch: task-executor]
    ImplExecutor --> ImplResult[Process: result.json]
    ImplResult --> ImplFinalize[track-state dispatch-finalize]
    ImplFinalize --> ImplPhase

    ImplNext -->|dispatch_phase_checker| ImplPhaseCheck[Dispatch: phase-checker]
    ImplPhaseCheck --> ImplCheckpoint[Checkpoint protocol]
    ImplCheckpoint --> ImplPhase

    ImplPhase --> ImplDone{Phase Done?}
    ImplDone -->|No| ImplNext
    ImplDone -->|Yes| ImplDone{All Phases Done?}
    ImplDone -->|No| ImplNext
    ImplDone -->|Yes| ImplFinal[Finalize + doc-syncer]
    ImplFinal --> ImplEnd([Implementation Complete])

    StatusFlow --> StatusRead[Read: track-state.json files]
    StatusRead --> StatusCompute[Compute track + phase status]
    StatusCompute --> StatusDisplay([Display Report])

    ReviewFlow --> ReviewSA[Dispatch: code-reviewer]
    ReviewSA --> ReviewResult[Parse: ---REVIEW RESULT---]
    ReviewResult --> ReviewReport([Review Report])

    RevertFlow --> RevertState[track-state recover]
    RevertState --> RevertGit[Git revert operations]
    RevertGit --> RevertSync[track-state sync]
    RevertSync --> RevertDone([Revert Complete])

    style Start fill:#e1f5ff
    style Skill fill:#4caf50
    style ImplNext fill:#ff9800
    style SetupDone fill:#9c27b0
    style NewTrackDone fill:#9c27b0
    style ImplEnd fill:#9c27b0
    style StatusDisplay fill:#9c27b0
    style ReviewReport fill:#9c27b0
    style RevertDone fill:#9c27b0
```

## Detailed Hook Flow: Tool Execution Lifecycle

```mermaid
stateDiagram-v2
    [*] --> ToolCallRequested: User invokes tool
    ToolCallRequested --> PreToolUseHook: PreToolUse event fires

    PreToolUseHook --> HookExecutes: Hook script runs
    HookExecutes --> ValidateCommand: Check git/state operations

    ValidateCommand --> Dangerous: Dangerous command detected
    ValidateCommand --> StateViolation: State lock violation
    ValidateCommand --> DirectModify: Direct state modification
    ValidateCommand --> Safe: Command is safe

    Dangerous --> BlockAsk: Ask user or block
    StateViolation --> BlockAsk
    DirectModify --> BlockAsk

    BlockAsk --> [*]: Exit code 2 (block)

    Safe --> Allow: Exit code 0 (allow)
    Allow --> ToolExecutes: Tool runs

    ToolExecutes --> PostToolUseHook: PostToolUse event fires

    PostToolUseHook --> AgentToolCheck: Is this Agent tool?

    AgentToolCheck --> YesAgent: Agent tool
    AgentToolCheck --> NoAgent: Other tool

    YesAgent --> FilterOutput: filter-subagent-output.py
    FilterOutput --> ResultBlockFound: ---RESULT--- block found?
    ResultBlockFound --> Yes: Extract filtered result
    ResultBlockFound --> No: Provide compact summary
    Yes --> [*]
    No --> [*]

    NoAgent --> TestCheck: Is this test command?
    TestCheck --> YesTest: Test command
    TestCheck --> NoTest: Non-test command

    YesTest --> LogResult: Log test result
    LogResult --> CheckFailure: Did test fail?
    CheckFailure --> YesFail: Inject TDD context
    CheckFailure --> NoFail: Normal completion
    YesFail --> [*]
    NoFail --> [*]

    NoTest --> [*]

    [*] --> ContinueMainSession: Main session continues
```

## Subagent Isolation: Context Loading Pattern

```mermaid
graph TB
    subgraph "Orchestrator (Minimal Context)"
        Orch[Orchestrator Agent]
        Orch -.->|dispatches| DispatchPrompt["TRACK_DIR=/path<br/>PHASE=0<br/>TASK=1<br/>NAME=task_name"]
    end

    subgraph "Subagent (Self-Loaded Context)"
        Sub[task-executor Agent]

        subgraph "Layer 0: Exploration Map"
            L0[exploration.md<br/>architecture<br/>gotchas<br/>file inventory]
        end

        subgraph "Layer 1: Task Identity"
            L1[plan.md<br/>task description<br/>AC/TC IDs]
        end

        subgraph "Layer 2: Acceptance Criteria"
            L2[spec.md<br/>relevant ACs<br/>test cases<br/>out-of-scope]
        end

        subgraph "Layer 3: Workflow & Style"
            L3A[task-workflow.md<br/>Steps 3-8]
            L3B[testing/strategy.md<br/>test conventions]
            L3C[code-styleguides/*<br/>language-specific rules]
        end

        subgraph "Layer 3.R: Retry Context"
            L3R[track-state get-handoff<br/>previous attempts<br/>failure details]
        end
    end

    DispatchPrompt --> Sub
    Sub --> L0
    Sub --> L1
    Sub --> L2
    Sub --> L3A
    Sub --> L3B
    Sub --> L3C
    Sub --> L3R

    Sub -->|executes TDD| Execution[TDD Workflow:<br/>Red→Green→Refactor]
    Execution -->|result.json| Result[""status": "SUCCESS",<br/>"commit_sha": "a1b2c3d",<br/>...]

    style Orch fill:#e1f5ff
    style Sub fill:#4caf50
    style L0 fill:#fff3e0
    style L1 fill:#ffe0b2
    style L2 fill:#ffcc80
    style L3A fill:#ffab91
    style L3B fill:#ffab91
    style L3C fill:#ffab91
    style L3R fill:#b388ff
```

## State Authority: Single Source of Truth

```mermaid
graph LR
    subgraph "CLI Layer (State Mutations)"
        CLI[track-state CLI]

        subgraph "Commands"
            C1[next]
            C2[lock]
            C3[complete]
            C4[fail]
            C5[skip]
            C6[sync-plan]
            C7[process-result]
            C8[finalize]
            C9[init]
            C10[recover]
        end
    end

    subgraph "State Layer (Authoritative)"
        State[track-state.json]
    end

    subgraph "Projection Layer (Derived)"
        Plan[plan.md<br/>status markers]
        Checklist[track-state.json<br/>evidence on tasks]
        Registry[tracks.md<br/>track entries]
    end

    subgraph "Audit Layer (Immutable)"
        Notes[Git Notes<br/>per-commit audit]
    end

    subgraph "Agents (Read-Only)"
        Orchestrator[Orchestrator<br/>NEVER edits state]
        Subagents[Subagents<br/>self-load from files]
    end

    CLI -->|reads/writes| State
    CLI -->|updates| Plan
    CLI -->|updates| Checklist
    CLI -->|updates| Registry
    CLI -->|writes| Notes

    State -.->|reads only| Orchestrator
    Plan -.->|reads only| Orchestrator
    State -.->|reads only| Subagents
    Plan -.->|reads only| Subagents

    Notes -.->|queries| Orchestrator

    style CLI fill:#e1f5ff
    style State fill:#ff9800
    style Plan fill:#4caf50
    style Checklist fill:#4caf50
    style Registry fill:#4caf50
    style Notes fill:#9c27b0
    style Orchestrator fill:#795548
    style Subagents fill:#795548
```

## Recovery Flow: Handling Interruptions

```mermaid
flowchart TD
    Start([Interruption]) --> CheckType{What was interrupted?}

    CheckType -->|Session crash| SessionRecovery[Session Recovery]
    CheckType -->|Subagent failure| SubagentRecovery[Subagent Recovery]
    CheckType -->|State inconsistency| StateRecovery[State Recovery]

    SessionRecovery --> SR1[User runs /conductor:implement]
    SR1 --> SR2[SessionStart hook loads session-handoff.md]
    SR2 --> SR3[Previous context injected]
    SR3 --> SR4[track-state recover detects stale locks]
    SR4 --> SR5[User prompted to recover or clean]
    SR5 --> SessionResume([Session Resumed])

    SubagentRecovery --> SAR1[SubagentStop hook detects failure]
    SAR1 --> SAR2{Critical agent?}
    SAR2 -->|Yes - task-executor/explorer/phase-checker| SAR3[Exit code 2 with asyncRewake]
    SAR2 -->|No| SAR4[Exit code 0 normal]
    SAR3 --> SAR5[Session wakes with recovery context]
    SAR5 --> SAR6[User prompted to continue]
    SAR6 --> SubagentResume([Subagent Resumed])

    StateRecovery --> STR1[track-state validate detects issues]
    STR1 --> STR2[Run track-state validate --fix]
    STR2 --> STR3[Auto-repairs:<br/>sync plan markers<br/>propagate status<br/>fix orphaned locks]
    STR3 --> StateResume([State Recovered])

    style Start fill:#f44336
    style SessionRecovery fill:#e1f5ff
    style SubagentRecovery fill:#4caf50
    style StateRecovery fill:#ff9800
    style SessionResume fill:#9c27b0
    style SubagentResume fill:#9c27b0
    style StateResume fill:#9c27b0
```

## Quality Gate Enforcement Flow

```mermaid
flowchart TD
    Commit([Task Executor Commits]) --> ProcessResult[track-state process-result]

    ProcessResult --> ReadResult[Read .conductor/result.json]
    ReadResult --> CheckTag{Task Tag?}

    CheckTag -->|Explore/Docs/Config/Chore/Manual| SkipTDD[Skip TDD Gate]
    CheckTag -->|Default| EnforceTDD[Enforce TDD Gate]

    EnforceTDD --> CheckTestFiles[Commit includes test files?]
    CheckTestFiles -->|No| FailTDD[FAIL: F2 Violation]
    CheckTestFiles -->|Yes| CheckCoverage

    SkipTDD --> CheckCoverage{Skip Coverage?}
    CheckCoverage -->|Yes| SuccessState[Set status: completed]
    CheckCoverage -->|No| CheckCoverage

    CheckCoverage[Run coverage tool] --> CoverageThreshold{Coverage >= 80%?}
    CoverageThreshold -->|No| FailCoverage[WARN: F3 Violation<br/>require user override]
    CoverageThreshold -->|Yes| SuccessState

    FailTDD --> Failure[Set status: failed<br/>increment retry_count]
    FailCoverage --> Failure

    Failure --> CommitGate[Commit with gate violations]
    SuccessState --> CommitSuccess[Commit with quality metrics]

    CommitGate --> WriteNotes[Write git notes with violations]
    CommitSuccess --> WriteNotes

    WriteNotes --> UpdateState[Update track-state.json]
    UpdateState --> SyncPlan[Sync plan.md markers]
    SyncPlan --> StoreEvidence[Store evidence on task in track-state.json]
    UpdateChecklist --> Return([Return to Orchestrator])

    style Commit fill:#4caf50
    style ProcessResult fill:#e1f5ff
    style FailTDD fill:#f44336
    style FailCoverage fill:#ff9800
    style SuccessState fill:#9c27b0
    style Failure fill:#f44336
    style CommitGate fill:#ff9800
    style CommitSuccess fill:#9c27b0
```

## Hook Communication: JSON Protocol

```mermaid
sequenceDiagram
    participant Runtime as Claude Runtime
    participant Hook as Hook Script
    participant Stdin as STDIN
    participant Stdout as STDOUT
    participant Stderr as STDERR

    Runtime->>Stdin: JSON input
    Note over Stdin: {"session_id": "abc",<br/>"hook_event_name": "PreToolUse",<br/>"tool_input": {...}}
    Stdin->>Hook: Pipe JSON
    Hook->>Hook: Process logic
    Hook->>Hook: Validate/Filter/Modify

    alt Exit code 0 (allow)
        Hook->>Stdout: JSON with additionalContext
        Note over Stdout: {"hookSpecificOutput":<br/>  {"hookEventName": "...",<br/>   "additionalContext": "..."}}
    else Exit code 2 (block)
        Hook->>Stderr: JSON with permissionDecision
        Note over Stderr: {"hookSpecificOutput":<br/>  {"permissionDecision": "ask",<br/>   "reason": "..."}}
        Hook->>Runtime: Exit code 2
    end

    Stdout->>Runtime: Parsed JSON
    Runtime->>Runtime: Inject additionalContext
    Runtime->>Runtime: Apply decision

    Note over Runtime,Hook: async hooks run in<br/>background, exit immediately
```

## Async vs Sync Hook Execution

```mermaid
flowchart TD
    HookEvent([Hook Event Fires]) --> Type{Hook Type?}

    Type -->|async: true| AsyncHook[Run in background]
    Type -->|asyncRewake: true| RewakeHook[Background + wake on exit 2]
    Type -->|neither| SyncHook[Block execution]

    AsyncHook --> AsyncLog[Log result to file]
    AsyncHook --> AsyncImmediate[Exit immediately]
    AsyncImmediate --> MainCont[Main session continues]

    RewakeHook --> RewakeExec[Run in background]
    RewakeExec --> RewakeCheck{Exit code?}

    RewakeCheck -->|0| RewakeLog[Log result]
    RewakeCheck -->|2| RewakeWake[Wake Claude immediately]
    RewakeCheck -->|other| RewakeLog

    RewakeLog --> MainCont
    RewakeWake --> MainCont

    SyncHook --> SyncExec[Run to completion]
    SyncExec --> SyncDecision{Exit code?}

    SyncDecision -->|0| SyncAllow[Allow operation]
    SyncDecision -->|2| SyncBlock[Block operation]
    SyncDecision -->|other| SyncAllow

    SyncAllow --> MainCont
    SyncBlock --> BlockMain[Main session blocked]
    BlockMain --> ErrorMsg[Show reason to user]
    ErrorMsg --> PromptUser[Prompt for action]

    style HookEvent fill:#e1f5ff
    style AsyncHook fill:#4caf50
    style RewakeHook fill:#ff9800
    style SyncHook fill:#9c27b0
    style MainCont fill:#795548
    style BlockMain fill:#f44336
```

## Complete Interaction Timeline

```mermaid
timeline
    title Conductor Track Implementation Timeline
    section Session Start
        SessionStart : Load conductor-core.md
        : Inject session handoff
        : Track-state recover
    section Task Execution
        Dispatch-Prepare : Commit "about to execute"
        SubagentStart : Inject role reminder
        Subagent Exec : Self-load context<br/>Execute TDD
        SubagentStop : Check for failures<br/>asyncRewake if critical
        PostToolUse : Filter result output
        Dispatch-Finalize : Update state<br/>Write git notes
        Commit : Final commit
    section Phase Boundary
        Phase-Done : Check phase complete
        Phase-Checker : Verify tests<br/>Manual check
        Checkpoint Commit : Phase checkpoint
        Plan Update : Add checkpoint SHA
    section Session End
        State-Check : Verify consistency
        Write Handoff : Save active state
        Log Metrics : Duration, operations
        Session End : Cleanup
```
