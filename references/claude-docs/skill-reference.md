# Extend Claude with Skills

Source: https://code.claude.com/docs/en/skills

Skills extend what Claude can do. Create a `SKILL.md` file with instructions, and Claude adds it to its toolkit. Claude uses skills when relevant, or you can invoke one directly with `/skill-name`.

Create a skill when you keep pasting the same instructions, checklist, or multi-step procedure into chat, or when a section of CLAUDE.md has grown into a procedure rather than a fact. Unlike CLAUDE.md content, a skill's body loads only when it's used, so long reference material costs almost nothing until you need it.

Custom commands have been merged into skills. A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way. Your existing `.claude/commands/` files keep working.

Claude Code skills follow the Agent Skills open standard, which works across multiple AI tools. Claude Code extends the standard with additional features like invocation control, subagent execution, and dynamic context injection.

---

## Bundled Skills

Claude Code includes a set of bundled skills: `/simplify`, `/batch`, `/debug`, `/loop`, `/claude-api`. Unlike most built-in commands, which execute fixed logic directly, bundled skills are prompt-based. Bundled skills are listed alongside built-in commands in the commands reference, marked **Skill** in the Purpose column.

---

## Getting Started

### Create Your First Skill

1. Create the skill directory:

```
mkdir -p ~/.claude/skills/summarize-changes
```

2. Write `SKILL.md` with YAML frontmatter and markdown instructions:

```yaml
---
description: Summarizes uncommitted changes and flags anything risky. Use when the user asks what changed, wants a commit message, or asks to review their diff.
---

## Current changes

!`git diff HEAD`

## Instructions

Summarize the changes above in two or three bullet points, then list any risks you notice such as missing error handling, hardcoded values, or tests that need updating. If the diff is empty, say there are no uncommitted changes.
```

3. Test the skill by asking Claude "What did I change?" or running `/summarize-changes`.

### Where Skills Live

| Location | Path | Applies to |
| --- | --- | --- |
| Enterprise | See managed settings | All users in your organization |
| Personal | `~/.claude/skills/<skill-name>/SKILL.md` | All your projects |
| Project | `.claude/skills/<skill-name>/SKILL.md` | This project only |
| Plugin | `<plugin>/skills/<skill-name>/SKILL.md` | Where plugin is enabled |

When skills share the same name across levels, enterprise overrides personal, and personal overrides project. Plugin skills use a `plugin-name:skill-name` namespace, so they cannot conflict with other levels. If a skill and a command share the same name, the skill takes precedence.

#### Live Change Detection

Claude Code watches skill directories for file changes. Adding, editing, or removing a skill under `~/.claude/skills/`, the project `.claude/skills/`, or a `.claude/skills/` inside an `--add-dir` directory takes effect within the current session without restarting.

#### Automatic Discovery from Nested Directories

When you work with files in subdirectories, Claude Code automatically discovers skills from nested `.claude/skills/` directories. For example, if you're editing a file in `packages/frontend/`, Claude Code also looks for skills in `packages/frontend/.claude/skills/`.

Each skill is a directory with `SKILL.md` as the entrypoint:

```
my-skill/
├── SKILL.md           # Main instructions (required)
├── template.md        # Template for Claude to fill in
├── examples/
│   └── sample.md      # Example output showing expected format
└── scripts/
    └── validate.sh    # Script Claude can execute
```

#### Skills from Additional Directories

The `--add-dir` flag grants file access rather than configuration discovery, but skills are an exception: `.claude/skills/` within an added directory is loaded automatically.

---

## Configure Skills

### Types of Skill Content

**Reference content** adds knowledge Claude applies to your current work. Conventions, patterns, style guides, domain knowledge. This content runs inline so Claude can use it alongside your conversation context.

```yaml
---
name: api-conventions
description: API design patterns for this codebase
---

When writing API endpoints:
- Use RESTful naming conventions
- Return consistent error formats
- Include request validation
```

**Task content** gives Claude step-by-step instructions for a specific action. These are often actions you want to invoke directly with `/skill-name`. Add `disable-model-invocation: true` to prevent Claude from triggering it automatically.

```yaml
---
name: deploy
description: Deploy the application to production
context: fork
disable-model-invocation: true
---

Deploy the application:
1. Run the test suite
2. Build the application
3. Push to the deployment target
```

Keep the body concise. Once a skill loads, its content stays in context across turns, so every line is a recurring token cost.

### Frontmatter Reference

All fields are optional. Only `description` is recommended.

| Field | Required | Description |
| --- | --- | --- |
| `name` | No | Display name for the skill. If omitted, uses the directory name. Lowercase letters, numbers, and hyphens only (max 64 characters). |
| `description` | Recommended | What the skill does and when to use it. Claude uses this to decide when to apply the skill. Combined with `when_to_use`, truncated at 1,536 characters. |
| `when_to_use` | No | Additional context for when Claude should invoke the skill. Appended to `description` and counts toward the 1,536-character cap. |
| `argument-hint` | No | Hint shown during autocomplete. Example: `[issue-number]` or `[filename] [format]`. |
| `arguments` | No | Named positional arguments for `$name` substitution. Accepts a space-separated string or a YAML list. |
| `disable-model-invocation` | No | Set to `true` to prevent Claude from automatically loading this skill. Also prevents preloading into subagents. Default: `false`. |
| `user-invocable` | No | Set to `false` to hide from the `/` menu. Default: `true`. |
| `allowed-tools` | No | Tools Claude can use without asking permission. Accepts a space-separated string or a YAML list. |
| `model` | No | Model to use when this skill is active. Accepts same values as `/model`, or `inherit`. |
| `effort` | No | Effort level: `low`, `medium`, `high`, `xhigh`, `max`. |
| `context` | No | Set to `fork` to run in a forked subagent context. |
| `agent` | No | Which subagent type to use when `context: fork` is set. |
| `hooks` | No | Hooks scoped to this skill's lifecycle. |
| `paths` | No | Glob patterns that limit when this skill is activated. |
| `shell` | No | Shell to use for `!`command`` and ```!``` blocks. `bash` (default) or `powershell`. |

### Available String Substitutions

| Variable | Description |
| --- | --- |
| `$ARGUMENTS` | All arguments passed when invoking the skill. |
| `$ARGUMENTS[N]` | Access a specific argument by 0-based index. |
| `$N` | Shorthand for `$ARGUMENTS[N]`. |
| `$name` | Named argument declared in the `arguments` frontmatter list. |
| `${CLAUDE_SESSION_ID}` | The current session ID. |
| `${CLAUDE_EFFORT}` | The current effort level. |
| `${CLAUDE_SKILL_DIR}` | The directory containing the skill's `SKILL.md` file. |

Indexed arguments use shell-style quoting, so wrap multi-word values in quotes.

### Add Supporting Files

Skills can include multiple files in their directory. Keep `SKILL.md` under 500 lines. Move detailed reference material to separate files.

```
my-skill/
├── SKILL.md (required - overview and navigation)
├── reference.md (detailed API docs - loaded when needed)
├── examples.md (usage examples - loaded when needed)
└── scripts/
    └── helper.py (utility script - executed, not loaded)
```

Reference supporting files from `SKILL.md`:

```markdown
## Additional resources

- For complete API details, see [reference.md](reference.md)
- For usage examples, see [examples.md](examples.md)
```

### Control Who Invokes a Skill

| Frontmatter | You can invoke | Claude can invoke | When loaded into context |
| --- | --- | --- | --- |
| (default) | Yes | Yes | Description always in context, full skill loads when invoked |
| `disable-model-invocation: true` | Yes | No | Description not in context, full skill loads when you invoke |
| `user-invocable: false` | No | Yes | Description always in context, full skill loads when invoked |

### Skill Content Lifecycle

When invoked, the rendered `SKILL.md` content enters the conversation as a single message and stays there for the rest of the session. Claude Code does not re-read the skill file on later turns.

Auto-compaction carries invoked skills forward within a token budget. When the conversation is summarized, Claude Code re-attaches the most recent invocation of each skill after the summary, keeping the first 5,000 tokens of each. Re-attached skills share a combined budget of 25,000 tokens.

### Pre-Approve Tools for a Skill

The `allowed-tools` field grants permission for the listed tools while the skill is active. It does not restrict which tools are available: every tool remains callable.

```yaml
---
name: commit
description: Stage and commit the current changes
disable-model-invocation: true
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *)
---
```

For project skills in `.claude/skills/`, `allowed-tools` takes effect after you accept the workspace trust dialog for that folder.

### Pass Arguments to Skills

Arguments are available via the `$ARGUMENTS` placeholder:

```yaml
---
name: fix-issue
description: Fix a GitHub issue
disable-model-invocation: true
---

Fix GitHub issue $ARGUMENTS following our coding standards.
```

Access individual arguments by position:

```yaml
---
name: migrate-component
description: Migrate a component from one framework to another
---

Migrate the $0 component from $1 to $2.
Preserve all existing behavior and tests.
```

If you invoke a skill with arguments but the skill doesn't include `$ARGUMENTS`, Claude Code appends `ARGUMENTS: <your input>` to the end of the skill content.

---

## Advanced Patterns

### Inject Dynamic Context

The `` !`<command>` `` syntax runs shell commands before the skill content is sent to Claude. The command output replaces the placeholder.

```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
allowed-tools: Bash(gh *)
---

## Pull request context
- PR diff: !`gh pr diff`
- PR comments: !`gh pr view --comments`
- Changed files: !`gh pr diff --name-only`

## Your task
Summarize this pull request...
```

For multi-line commands, use a fenced code block opened with `` ```! ``:

````markdown
## Environment
```!
node --version
npm --version
git status --short
```
````

To disable this behavior, set `"disableSkillShellExecution": true` in settings.

To request deeper reasoning when a skill runs, include `ultrathink` anywhere in the skill content.

### Run Skills in a Subagent

Add `context: fork` to run a skill in isolation. The skill content becomes the prompt that drives the subagent. It won't have access to your conversation history.

| Approach | System prompt | Task | Also loads |
| --- | --- | --- | --- |
| Skill with `context: fork` | From agent type (`Explore`, `Plan`, etc.) | SKILL.md content | CLAUDE.md |
| Subagent with `skills` field | Subagent's markdown body | Claude's delegation message | Preloaded skills + CLAUDE.md |

#### Example: Research Skill Using Explore Agent

```yaml
---
name: deep-research
description: Research a topic thoroughly
context: fork
agent: Explore
---

Research $ARGUMENTS thoroughly:

1. Find relevant files using Glob and Grep
2. Read and analyze the code
3. Summarize findings with specific file references
```

The `agent` field specifies which subagent configuration to use. Options include built-in agents (`Explore`, `Plan`, `general-purpose`) or any custom subagent from `.claude/agents/`. If omitted, uses `general-purpose`.

### Restrict Claude's Skill Access

Three ways to control which skills Claude can invoke:

**Disable all skills** by denying the Skill tool in `/permissions`:
```
Skill
```

**Allow or deny specific skills** using permission rules:
```
Skill(commit)
Skill(review-pr *)
Skill(deploy *)
```

**Hide individual skills** by adding `disable-model-invocation: true` to their frontmatter.

### Override Skill Visibility from Settings

The `skillOverrides` setting controls skill visibility from your settings:

| Value | Listed to Claude | In `/` menu |
| --- | --- | --- |
| `"on"` | Name and description | Yes |
| `"name-only"` | Name only | Yes |
| `"user-invocable-only"` | Hidden | Yes |
| `"off"` | Hidden | Hidden |

```json
{
  "skillOverrides": {
    "legacy-context": "name-only",
    "deploy": "off"
  }
}
```

Plugin skills are not affected by `skillOverrides`. Manage those through `/plugin` instead.

---

## Share Skills

- **Project skills**: Commit `.claude/skills/` to version control
- **Plugins**: Create a `skills/` directory in your plugin
- **Managed**: Deploy organization-wide through managed settings

---

## Troubleshooting

### Skill Not Triggering

1. Check the description includes keywords users would naturally say
2. Verify the skill appears in `What skills are available?`
3. Try rephrasing your request to match the description more closely
4. Invoke it directly with `/skill-name` if the skill is user-invocable

### Skill Triggers Too Often

1. Make the description more specific
2. Add `disable-model-invocation: true` if you only want manual invocation

### Skill Descriptions Are Cut Short

Skill descriptions are loaded into context so Claude knows what's available. The budget scales dynamically at 1% of the context window, with a fallback of 8,000 characters. Each entry's combined `description` + `when_to_use` text is capped at 1,536 characters.

To raise the limit, set the `SLASH_COMMAND_TOOL_CHAR_BUDGET` environment variable. To free budget, set low-priority entries to `"name-only"` in `skillOverrides`.
