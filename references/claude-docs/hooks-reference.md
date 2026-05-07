# Hooks Reference

Source: https://code.claude.com/docs/en/hooks

Hooks are user-defined shell commands, HTTP endpoints, or LLM prompts that execute automatically at specific points in Claude Code's lifecycle. Use this reference to look up event schemas, configuration options, JSON input/output formats, and advanced features like async hooks, HTTP hooks, and MCP tool hooks.

## Hook lifecycle

Hooks fire at specific points during a Claude Code session. When an event fires and a matcher matches, Claude Code passes JSON context about the event to your hook handler. For command hooks, input arrives on stdin. For HTTP hooks, it arrives as the POST request body. Your handler can then inspect the input, take action, and optionally return a decision. Events fall into three cadences: once per session (`SessionStart`, `SessionEnd`), once per turn (`UserPromptSubmit`, `Stop`, `StopFailure`), and on every tool call inside the agentic loop (`PreToolUse`, `PostToolUse`).

| Event | When it fires |
| --- | --- |
| `SessionStart` | When a session begins or resumes |
| `Setup` | When you start Claude Code with `--init-only`, or with `--init` or `--maintenance` in `-p` mode |
| `UserPromptSubmit` | When you submit a prompt, before Claude processes it |
| `UserPromptExpansion` | When a user-typed command expands into a prompt, before it reaches Claude |
| `PreToolUse` | Before a tool call executes. Can block it |
| `PermissionRequest` | When a permission dialog appears |
| `PermissionDenied` | When a tool call is denied by the auto mode classifier |
| `PostToolUse` | After a tool call succeeds |
| `PostToolUseFailure` | After a tool call fails |
| `PostToolBatch` | After a full batch of parallel tool calls resolves, before the next model call |
| `Notification` | When Claude Code sends a notification |
| `SubagentStart` | When a subagent is spawned |
| `SubagentStop` | When a subagent finishes |
| `TaskCreated` | When a task is being created via `TaskCreate` |
| `TaskCompleted` | When a task is being marked as completed |
| `Stop` | When Claude finishes responding |
| `StopFailure` | When the turn ends due to an API error |
| `TeammateIdle` | When an agent team teammate is about to go idle |
| `InstructionsLoaded` | When a CLAUDE.md or `.claude/rules/*.md` file is loaded into context |
| `ConfigChange` | When a configuration file changes during a session |
| `CwdChanged` | When the working directory changes |
| `FileChanged` | When a watched file changes on disk |
| `WorktreeCreate` | When a worktree is being created |
| `WorktreeRemove` | When a worktree is being removed |
| `PreCompact` | Before context compaction |
| `PostCompact` | After context compaction completes |
| `Elicitation` | When an MCP server requests user input during a tool call |
| `ElicitationResult` | After a user responds to an MCP elicitation |
| `SessionEnd` | When a session terminates |

## Configuration

Hooks are defined in JSON settings files with three levels of nesting:
1. Choose a hook event to respond to
2. Add a matcher group to filter when it fires
3. Define one or more hook handlers to run when matched

### Hook locations

| Location | Scope | Shareable |
| --- | --- | --- |
| `~/.claude/settings.json` | All your projects | No |
| `.claude/settings.json` | Single project | Yes |
| `.claude/settings.local.json` | Single project | No |
| Managed policy settings | Organization-wide | Yes |
| Plugin `hooks/hooks.json` | When plugin is enabled | Yes |
| Skill or agent frontmatter | While the component is active | Yes |

### Matcher patterns

| Matcher value | Evaluated as | Example |
| --- | --- | --- |
| `"*"`, `""`, or omitted | Match all | fires on every occurrence |
| Only letters, digits, `_`, and `\|` | Exact string, or `\|`-separated list | `Bash`, `Edit\|Write` |
| Contains any other character | JavaScript regular expression | `^Notebook`, `mcp__memory__.*` |

### Hook handler types

- **Command hooks** (`type: "command"`): run a shell command
- **HTTP hooks** (`type: "http"`): send HTTP POST request
- **MCP tool hooks** (`type: "mcp_tool"`): call a tool on an MCP server
- **Prompt hooks** (`type: "prompt"`): send a prompt to a Claude model
- **Agent hooks** (`type: "agent"`): spawn a subagent with tool access

### Common fields

| Field | Required | Description |
| --- | --- | --- |
| `type` | yes | `"command"`, `"http"`, `"mcp_tool"`, `"prompt"`, or `"agent"` |
| `if` | no | Permission rule syntax to filter when hook runs |
| `timeout` | no | Seconds before canceling. Defaults: 600 command, 30 prompt, 60 agent |
| `statusMessage` | no | Custom spinner message displayed while the hook runs |
| `once` | no | If `true`, runs once per session then removed (skill frontmatter only) |

## Hook input and output

### Common input fields

| Field | Description |
| --- | --- |
| `session_id` | Current session identifier |
| `transcript_path` | Path to conversation JSON |
| `cwd` | Current working directory |
| `permission_mode` | Current permission mode |
| `hook_event_name` | Name of the event that fired |

### Exit code output

- **Exit 0**: success, stdout parsed for JSON output
- **Exit 2**: blocking error, stderr fed back to Claude
- **Any other**: non-blocking error, execution continues

### JSON output fields

| Field | Default | Description |
| --- | --- | --- |
| `continue` | `true` | If `false`, Claude stops processing |
| `stopReason` | none | Message shown when `continue` is `false` |
| `suppressOutput` | `false` | If `true`, omits stdout from debug log |
| `systemMessage` | none | Warning message shown to the user |

## Event details

### SessionStart

Runs when a session begins or resumes. Matcher: `startup`, `resume`, `clear`, `compact`.

Additional input fields: `source`, `model`, optionally `agent_type`.

Can return `additionalContext` and persist env vars via `CLAUDE_ENV_FILE`.

### Setup

Fires only with `--init-only`, or `--init`/`--maintenance` in `-p` mode. Matcher: `init`, `maintenance`.

### UserPromptSubmit

Runs when user submits a prompt. Can block with `decision: "block"` or add `additionalContext`.

### UserPromptExpansion

Runs when a slash command expands. Matches on `command_name`. Can block expansion.

### PreToolUse

Runs before tool execution. Decision control via `hookSpecificOutput`:
- `permissionDecision`: `"allow"`, `"deny"`, `"ask"`, `"defer"`
- `permissionDecisionReason`: reason string
- `updatedInput`: modifies tool input
- `additionalContext`: context for Claude

### PostToolUse

Runs after tool succeeds. Can return `decision`, `reason`, `additionalContext`, `updatedToolOutput`.

### PostToolUseFailure

Runs after tool failure. Can return `additionalContext`.

### PostToolBatch

Runs after all parallel tool calls resolve. Can return `additionalContext` or `decision: "block"`.

### Stop

Runs when Claude finishes. Can block with `decision: "block"` and `reason`.

### StopFailure

Runs on API error. No decision control, logging only.

### PermissionRequest

Runs when permission dialog appears. Decision via `hookSpecificOutput.decision.behavior`: `"allow"` or `"deny"`.

### PermissionDenied

Runs when auto mode classifier denies. Can return `hookSpecificOutput.retry: true`.

### Notification

Runs on notifications. No blocking, side effects only.

### SubagentStart / SubagentStop

Run when subagents spawn/finish. Can inject `additionalContext` into subagent.

### TaskCreated / TaskCompleted

Run on task lifecycle events. Exit 2 rolls back / prevents completion.

### TeammateIdle

Runs when teammate about to go idle. Exit 2 keeps teammate working.

### ConfigChange

Runs on settings changes. Can block with `decision: "block"` (except `policy_settings`).

### CwdChanged

Runs on directory changes. Can return `watchPaths` for dynamic file watching.

### FileChanged

Runs on watched file changes. Can return `watchPaths`.

### WorktreeCreate / WorktreeRemove

Control worktree creation/removal. WorktreeCreate must return worktree path.

### PreCompact / PostCompact

Run around compaction. PreCompact can block.

### Elicitation / ElicitationResult

MCP server user input. Can accept/decline/cancel programmatically.

### SessionEnd

Runs on session termination. Cleanup/logging only. Default timeout 1.5s.

## Prompt-based hooks

`type: "prompt"` sends hook input + prompt to an LLM for evaluation.

Response schema: `{ "ok": true/false, "reason": "..." }`

## Agent-based hooks

`type: "agent"` spawns a subagent with tool access for verification (up to 50 turns).

Same response schema as prompt hooks.

## Async hooks

`"async": true` runs command hooks in background without blocking Claude.
`"asyncRewake": true` wakes Claude on exit code 2.

## Environment variables for hooks

- `$CLAUDE_PROJECT_DIR`: project root
- `${CLAUDE_PLUGIN_ROOT}`: plugin installation directory
- `${CLAUDE_PLUGIN_DATA}`: plugin persistent data directory
- `$CLAUDE_ENV_FILE`: file path for persisting env vars across session

## Security considerations

- Validate and sanitize inputs
- Always quote shell variables
- Block path traversal
- Use absolute paths
- Skip sensitive files
