# Create Custom Subagents

Source: https://code.claude.com/docs/en/sub-agents

Subagents are specialized AI assistants that handle specific types of tasks in their own context window with custom system prompts, specific tool access, and independent permissions. When Claude encounters a matching task, it delegates to the subagent which returns only a summary.

## Built-in Subagents

| Agent | Model | Tools | Purpose |
| --- | --- | --- | --- |
| Explore | Haiku | Read-only | File discovery, code search, codebase exploration |
| Plan | Inherits | Read-only | Codebase research for planning |
| General-purpose | Inherits | All | Complex research, multi-step operations |
| statusline-setup | Sonnet | — | `/statusline` configuration |
| claude-code-guide | Haiku | — | Claude Code feature questions |

## Subagent Scope and Priority

| Location | Scope | Priority |
| --- | --- | --- |
| Managed settings | Organization-wide | 1 (highest) |
| `--agents` CLI flag | Current session | 2 |
| `.claude/agents/` | Current project | 3 |
| `~/.claude/agents/` | All your projects | 4 |
| Plugin's `agents/` directory | Where plugin is enabled | 5 (lowest) |

## Frontmatter Fields

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Unique identifier, lowercase letters and hyphens |
| `description` | Yes | When Claude should delegate to this subagent |
| `tools` | No | Tools the subagent can use (allowlist). Inherits all if omitted |
| `disallowedTools` | No | Tools to deny (denylist) |
| `model` | No | `sonnet`, `opus`, `haiku`, full model ID, or `inherit` (default) |
| `permissionMode` | No | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan` |
| `maxTurns` | No | Maximum agentic turns |
| `skills` | No | Skills to preload into subagent context |
| `mcpServers` | No | MCP servers available to this subagent |
| `hooks` | No | Lifecycle hooks scoped to this subagent |
| `memory` | No | Persistent memory scope: `user`, `project`, or `local` |
| `background` | No | `true` to always run as background task |
| `effort` | No | Effort level: `low`, `medium`, `high`, `xhigh`, `max` |
| `isolation` | No | `worktree` for isolated git worktree copy |
| `color` | No | Display color: `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan` |
| `initialPrompt` | No | Auto-submitted first user turn when running as main agent |

## Model Resolution Order

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
2. Per-invocation `model` parameter
3. Subagent definition's `model` frontmatter
4. Main conversation's model

## Tool Access Control

- `tools` (allowlist): exclusively allow listed tools
- `disallowedTools` (denylist): inherit all except listed tools
- If both set: `disallowedTools` applied first, then `tools` resolved against remaining pool
- Restrict subagent spawning: `Agent(worker, researcher)` in `tools` field

## MCP Server Configuration

Each entry is either an inline server definition or a string reference:

```yaml
mcpServers:
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  - github  # reference by name
```

## Permission Modes

| Mode | Behavior |
| --- | --- |
| `default` | Standard permission checking with prompts |
| `acceptEdits` | Auto-accept file edits in working directory |
| `auto` | Background classifier reviews commands |
| `dontAsk` | Auto-deny prompts (allowed tools still work) |
| `bypassPermissions` | Skip permission prompts |
| `plan` | Read-only exploration |

Parent `bypassPermissions` or `acceptEdits` takes precedence. Parent auto mode is inherited regardless of subagent setting.

## Persistent Memory

| Scope | Location | Use when |
| --- | --- | --- |
| `user` | `~/.claude/agent-memory/<name>/` | Cross-project learnings |
| `project` | `.claude/agent-memory/<name>/` | Project-specific, shareable via VCS |
| `local` | `.claude/agent-memory-local/<name>/` | Project-specific, not in VCS |

## Hooks in Subagents

### Frontmatter hooks (subagent-scoped)

```yaml
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-command.sh"
  PostToolUse:
    - matcher: "Edit|Write"
      hooks:
        - type: command
          command: "./scripts/run-linter.sh"
```

`Stop` hooks in frontmatter are auto-converted to `SubagentStop` at runtime.

### Project-level hooks (settings.json)

| Event | Matcher | When |
| --- | --- | --- |
| `SubagentStart` | Agent type name | Subagent begins |
| `SubagentStop` | Agent type name | Subagent completes |

## Invocation Patterns

- **Natural language**: "Use the test-runner subagent to fix failing tests"
- **@-mention**: `@"code-reviewer (agent)" look at the auth changes`
- **Session-wide**: `claude --agent code-reviewer` or `{ "agent": "code-reviewer" }` in settings
- **CLI agents**: `claude --agents '{"code-reviewer": {...}}'`

## Foreground vs Background

- **Foreground**: blocks main conversation, passes permission prompts through
- **Background**: concurrent, pre-approves permissions, auto-denies unapproved. Press **Ctrl+B** to background a running task
- Disable: `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`

## Fork Mode

A fork inherits the full conversation history instead of starting fresh.

| | Fork | Named subagent |
| --- | --- | --- |
| Context | Full conversation history | Fresh context |
| System prompt | Same as main | From definition file |
| Model | Same as main | From `model` field |
| Permissions | Prompts surface in terminal | Pre-approved |
| Prompt cache | Shared with main | Separate |

Enable: `CLAUDE_CODE_FORK_SUBAGENT=1`

- `/fork <directive>` spawns a fork
- Forks always run in background
- A fork cannot spawn further forks

## Context Management

- Auto-compaction at ~95% capacity (configurable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`)
- Subagent transcripts stored at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`
- Resume subagents via `SendMessage` tool with agent ID
- Transcripts persist independently of main conversation compaction

## Disabling Subagents

```json
{
  "permissions": {
    "deny": ["Agent(Explore)", "Agent(my-custom-agent)"]
  }
}
```

Or: `claude --disallowedTools "Agent(Explore)"`
