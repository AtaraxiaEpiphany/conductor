---
title: Troubleshooting
audience: user
status: stable
last_updated: 2026-05-11
related:
  - getting-started.md
  - user-guide.md
---

# Troubleshooting

> Common issues and solutions for Conductor

---

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Setup Issues](#setup-issues)
3. [Track Creation Issues](#track-creation-issues)
4. [Execution Issues](#execution-issues)
5. [State Issues](#state-issues)
6. [Quality Gate Violations](#quality-gate-violations)
7. [Hook Issues](#hook-issues)

---

## Installation Issues

### Plugin Not Loading

**Symptoms**: Commands like `/conductor:setup` not recognized

**Solutions**:

1. Check plugin is enabled:
   ```bash
   claude plugin list
   ```

2. Enable plugin if needed:
   ```bash
   claude plugin enable conductor
   ```

3. Verify plugin directory exists:
   ```bash
   ls ~/.claude/plugins/conductor
   ```

4. Check for errors:
   ```bash
   claude --debug
   ```

---

### Module Import Errors

**Symptoms**: Python script fails with import errors

**Solutions**:

1. Check Python version (requires 3.8+):
   ```bash
   python3 --version
   ```

2. Verify lib/ directory exists:
   ```bash
   ls scripts/lib/
   ```

3. Check script permissions:
   ```bash
   chmod +x scripts/*.py
   ```

---

## Setup Issues

### Setup Hangs During Analysis

**Symptoms**: `/conductor:setup` hangs after "Analyzing project..."

**Solutions**:

1. Check for large files:
   ```bash
   find . -size +10M -type f
   ```

2. Exclude large directories:
   - Add to `.claudeignore`
   - Add to `conductor/ignore-dirs.md`

3. Run with timeout:
   ```
   > /conductor:setup --timeout 60
   ```

---

### Product Definition Not Saved

**Symptoms**: Product definition lost after setup

**Solutions**:

1. Check for gitignore conflicts:
   ```bash
   cat .gitignore | grep conductor
   ```

2. Verify file was committed:
   ```bash
   git log --oneline conductor/overview/product.md
   ```

3. Check file permissions:
   ```bash
   ls -la conductor/overview/
   ```

---

## Track Creation Issues

### Spec Generation Fails

**Symptoms**: `conductor:spec-planner` fails or produces poor specs

**Solutions**:

1. Provide more detailed requirements:
   ```
   > /conductor:newTrack Implement OAuth2 login with support for
   Google, GitHub, and email/password authentication.
   Include session management and token refresh.
   ```

2. Reference existing documentation:
   - Ensure related docs are in project
   - Update conductor/index.md with references

3. Regenerate spec:
   - Run `/conductor:review` on existing spec
   - Make corrections
   - Re-run `/conductor:newTrack`

---

### Plan Generation Creates Too Many Tasks

**Symptoms**: plan.md has 50+ tasks, overwhelming

**Solutions**:

1. Reduce scope:
   - Split into multiple tracks
   - Focus on core features first

2. Use task grouping:
   - Combine related tasks
   - Use subtasks for details

3. Adjust granularity:
   - Larger tasks take 2-4 hours
   - Smaller tasks take 1-2 hours

---

## Execution Issues

### Implement Hangs

**Symptoms**: `/conductor:implement` hangs without progress

**Solutions**:

1. Check for stuck subagent:
   ```bash
   > /conductor:status
   ```

2. Review last actions:
   - Check session history
   - Identify stuck operation

3. Force recovery:
   ```
   > /conductor:revert all
   > /conductor:implement
   ```

4. Enable debug mode:
   ```bash
   export CONDUCTOR_DEBUG=1
   ```

---

### Task Fails Repeatedly

**Symptoms**: Same task fails after 3+ attempts

**Solutions**:

1. Check task requirements:
   - Verify ACs are achievable
   - Check for missing dependencies

2. Update task description:
   - Add more details
   - Break into smaller tasks

3. Skip task (if appropriate):
   ```
   > /conductor:implement
   # Answer: Skip this task
   ```

4. Manual implementation:
   - Implement task manually
   - Mark as completed with commit SHA

---

### Phase Checkpoint Fails

**Symptoms**: phase-checker reports verification failures

**Solutions**:

1. Review checkpoint requirements:
   ```bash
   cat conductor/workflow/phase-checkpoint.md
   ```

2. Check test coverage:
   ```bash
   npm run coverage  # or equivalent
   ```

3. Verify manual testing:
   - Complete all manual verification steps
   - Document results

4. Fix issues and re-run:
   ```
   > /conductor:implement
   # Will trigger phase-checker again
   ```

---

## State Issues

### Stale Lock Detected

**Symptoms**: Status shows task in_progress but no work is happening

**Solutions**:

1. Identify stale lock:
   ```bash
   > /conductor:status --health
   ```

2. Clean up stale locks:
   ```bash
   track-state validate --fix conductor/tracks/<track_id>
   ```

3. Manual reset:
   ```
   > /conductor:revert task "<task_name>"
   ```

---

### State Inconsistency

**Symptoms**: track-state.json and plan.md show different states

**Solutions**:

1. Validate state:
   ```bash
   track-state validate <track-dir>
   ```

2. Sync plan:
   ```bash
   track-state sync-plan <track-dir>
   ```

3. Auto-fix:
   ```bash
   track-state validate --fix <track-dir>
   ```

---

## Quality Gate Violations

### F1 Violation (Global State Lock)

**Symptoms**: "Multiple in_progress tasks detected"

**Solutions**:

1. Identify duplicate locks:
   ```bash
   > /conductor:status
   ```

2. Resolve stale lock:
   ```
   > /conductor:revert task "<task_name>"
   ```

3. Continue execution:
   ```
   > /conductor:implement
   ```

---

### F2 Violation (TDD Gate)

**Symptoms**: "No tests in commit" error

**Solutions**:

1. Verify test file exists:
   ```bash
   ls test/ | grep <feature>
   ```

2. Add test file to commit:
   ```bash
   git add test/<feature>.test.ts
   git commit --amend
   ```

3. Or mark task as exempt:
   - Add `[Config]`, `[Docs]`, or `[Chore]` tag to task in plan.md

---

### F3 Violation (Coverage Gate)

**Symptoms**: "Coverage < 80%" warning

**Solutions**:

1. Run coverage analysis:
   ```bash
   npm run coverage
   ```

2. Identify untested code:
   - Review coverage report
   - Add tests for low-coverage areas

3. Improve coverage to 80%+:
   - Add integration tests
   - Add edge case tests

4. Or exempt task:
   - Mark as `[Config]`, `[Docs]`, `[Chore]`, or `[Manual]`

---

### F4 Violation (SHA Must Exist)

**Symptoms**: "Missing commit SHA" error

**Solutions**:

1. Find commit SHA:
   ```bash
   git log --oneline | head -1
   ```

2. Append SHA to task:
   ```markdown
   - [x] Task description [a1b2c3d]
   ```

3. Sync plan:
   ```bash
   track-state sync-plan <track-dir>
   ```

---

## Hook Issues

### Hook Not Executing

**Symptoms**: Hook doesn't seem to run

**Solutions**:

1. Verify hook is configured:
   ```bash
   cat hooks/hooks.json
   ```

2. Check script permissions:
   ```bash
   ls -la scripts/<hook>.py
   ```

3. Make script executable:
   ```bash
   chmod +x scripts/<hook>.py
   ```

4. Test hook manually:
   ```bash
   echo '{}' | python3 scripts/<hook>.py
   ```

---

### Hook Timeout

**Symptoms**: Hook execution times out

**Solutions**:

1. Increase timeout in hooks.json:
   ```json
   {
     "type": "command",
     "command": "...",
     "timeout": 30  // Increase from default
   }
   ```

2. Optimize hook script:
   - Reduce file I/O
   - Cache results where appropriate

---

## Getting Help

### Debug Mode

Enable debug logging:

```bash
export CONDUCTOR_DEBUG=1
```

Logs are written to:
- `logs/hook-debug.log`
- `logs/task-lifecycle.log`
- `logs/session-debug.log`

### Log Collection

Gather logs for troubleshooting:

```bash
tar -czf conductor-logs.tar.gz logs/
```

### Report Issue

When reporting issues, include:
1. Conductor version (see CHANGELOG.md)
2. Python version (`python3 --version`)
3. Claude Code version
4. Error messages
5. Relevant logs
6. Steps to reproduce

---

## Next Steps

- [Getting Started](getting-started.md) - Quick start guide
- [User Guide](user-guide.md) - Complete usage guide
- [Commands Reference](commands.md) - Command details

---

**Last Updated**: 2026-05-11
