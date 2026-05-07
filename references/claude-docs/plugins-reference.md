# Claude Code Plugins Reference

> Source: https://code.claude.com/docs/en/plugins-reference

## Overview

A **plugin** is a self-contained directory of components that extends Claude Code with custom functionality. Plugin components include skills, agents, hooks, MCP servers, LSP servers, and monitors.

---

## Plugin Components

### Skills

- **Location**: `skills/` or `commands/` directory in plugin root
- **Format**: Skills are directories with `SKILL.md`; commands are flat `.md` files
- **Structure**:
  ```
  skills/
  ├── pdf-processor/
  │   ├── SKILL.md
  │   ├── reference.md (optional)
  │   └── scripts/ (optional)
  ```
- Auto-discovered on install; Claude can invoke them based on task context

### Agents

- **Location**: `agents/` directory in plugin root
- **Format**: Markdown files with frontmatter (`name`, `description`, `model`, `effort`, `maxTurns`, `tools`, `disallowedTools`, `skills`, `memory`, `background`, `isolation`)
- Only valid `isolation` value: `"worktree"`
- **Security**: `hooks`, `mcpServers`, and `permissionMode` are NOT supported for plugin-shipped agents
- Appear in `/agents` interface; Claude can invoke automatically or manually

### Hooks

- **Location**: `hooks/hooks.json` or inline in `plugin.json`
- **Format**: JSON with event matchers and actions
- **Hook types**: `command`, `http`, `mcp_tool`, `prompt`, `agent`
- **Supported events**: `SessionStart`, `Setup`, `UserPromptSubmit`, `UserPromptExpansion`, `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `Notification`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `Stop`, `StopFailure`, `TeammateIdle`, `InstructionsLoaded`, `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `PreCompact`, `PostCompact`, `Elicitation`, `ElicitationResult`, `SessionEnd`

### MCP Servers

- **Location**: `.mcp.json` or inline in `plugin.json`
- **Format**: Standard MCP server configuration with `command`, `args`, `env`
- Auto-start when plugin enabled; appear as standard MCP tools

### LSP Servers

- **Location**: `.lsp.json` or inline in `plugin.json`
- **Format**: JSON mapping language server names to configs
- **Required fields**: `command`, `extensionToLanguage`
- **Optional fields**: `args`, `transport`, `env`, `initializationOptions`, `settings`, `workspaceFolder`, `startupTimeout`, `shutdownTimeout`, `restartOnCrash`, `maxRestarts`
- Language server binary must be installed separately

### Monitors

- **Location**: `monitors/monitors.json` or inline in `plugin.json`
- **Format**: JSON array of monitor entries
- **Required fields**: `name`, `command`, `description`
- **Optional fields**: `when` (`"always"` default, or `"on-skill-invoke:<skill-name>"`)
- Require Claude Code v2.1.105+
- Run only in interactive CLI sessions

### Themes

- **Location**: `themes/` directory
- **Format**: JSON with `base` preset and `overrides` color tokens
- Plugin themes are read-only; users can copy to edit

---

## Plugin Installation Scopes

| Scope | Settings file | Use case |
|-------|--------------|----------|
| `user` | `~/.claude/settings.json` | Personal plugins (default) |
| `project` | `.claude/settings.json` | Team-shared via VCS |
| `local` | `.claude/settings.local.json` | Project-specific, gitignored |
| `managed` | Managed settings | Read-only, update only |

---

## Plugin Manifest Schema

**File**: `.claude-plugin/plugin.json` — only `name` is required.

### Key Fields

| Field | Type | Purpose |
|-------|------|---------|
| `name` | string | Unique identifier (kebab-case) |
| `version` | string | Semver version (optional) |
| `description` | string | Brief description |
| `author` | object | Author info |
| `skills` | string\|array | Custom skill directories |
| `commands` | string\|array | Custom command files |
| `agents` | string\|array | Custom agent files |
| `hooks` | string\|array\|object | Hook config paths or inline |
| `mcpServers` | string\|array\|object | MCP config paths or inline |
| `lspServers` | string\|array\|object | LSP configs |
| `monitors` | string\|array | Monitor configs |
| `themes` | string\|array | Theme files/directories |
| `userConfig` | object | User-configurable values |
| `channels` | array | Channel declarations for message injection |
| `dependencies` | array | Required plugins with optional semver constraints |

### User Configuration (`userConfig`)

Declares values prompted at enable time. Types: `string`, `number`, `boolean`, `directory`, `file`. Options: `sensitive`, `required`, `default`, `multiple`, `min`/`max`. Available as `${user_config.KEY}` in configs and `CLAUDE_PLUGIN_OPTION_<KEY>` env vars.

### Channels

Binds MCP servers as message channels (e.g., Telegram, Slack). Each channel has a `server` (matching an MCP server key) and optional `userConfig`.

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin install directory (changes on update) |
| `${CLAUDE_PLUGIN_DATA}` | Persistent data directory surviving updates (`~/.claude/plugins/data/{id}/`) |

Both are substituted in skill/agent content, hook commands, monitor commands, MCP/LSP configs, and exported as env vars to subprocesses.

### Persistent Data Pattern

Recommended: use `SessionStart` hook to diff bundled manifest against stored copy and reinstall dependencies when they differ.

---

## Plugin Directory Structure

```
plugin/
├── .claude-plugin/
│   └── plugin.json          # Manifest (optional)
├── skills/                   # Skills
├── commands/                 # Flat .md skills
├── agents/                   # Subagent definitions
├── output-styles/            # Output styles
├── themes/                   # Color themes
├── monitors/                 # Background monitors
│   └── monitors.json
├── hooks/                    # Hook configs
│   └── hooks.json
├── bin/                      # Executables added to PATH
├── settings.json             # Default settings
├── .mcp.json                 # MCP servers
├── .lsp.json                 # LSP servers
├── scripts/                  # Hook/utility scripts
├── LICENSE
└── CHANGELOG.md
```

**Important**: Only `plugin.json` goes in `.claude-plugin/`. All other component directories must be at plugin root. A `CLAUDE.md` at plugin root is NOT loaded as project context.

---

## CLI Commands

| Command | Purpose |
|---------|---------|
| `claude plugin install <plugin> [-s scope]` | Install from marketplace |
| `claude plugin uninstall <plugin> [-s scope] [--keep-data] [--prune]` | Remove plugin |
| `claude plugin prune [-s scope] [--dry-run]` | Remove orphaned dependencies |
| `claude plugin enable <plugin> [-s scope]` | Enable disabled plugin |
| `claude plugin disable <plugin> [-s scope]` | Disable without uninstalling |
| `claude plugin update <plugin> [-s scope]` | Update to latest version |
| `claude plugin list [--json] [--available]` | List installed plugins |
| `claude plugin tag [--push] [--dry-run] [-f]` | Create release git tag |

---

## Version Management

Two approaches:

| Approach | Method | Update behavior |
|----------|--------|----------------|
| **Explicit version** | Set `version` in `plugin.json` | Updates only on version bump |
| **Commit-SHA version** | Omit `version` | Updates on every new commit |

Version resolution order: `plugin.json` version → marketplace entry version → git commit SHA → `unknown`.

---

## Caching and File Resolution

- Marketplace plugins are copied to `~/.claude/plugins/cache`
- Each version is a separate directory; old versions orphaned for 7 days then auto-removed
- **No path traversal**: plugins cannot reference files outside their directory
- **Symlinks**: supported and preserved in cache for external dependency access

---

## Debugging

- Run `claude --debug` to see plugin loading details
- Run `claude plugin validate` or `/plugin validate` to check manifest, skill/agent frontmatter, and hooks JSON
- Common issues: wrong directory structure, non-executable scripts, missing `${CLAUDE_PLUGIN_ROOT}`, absolute paths
