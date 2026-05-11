---
title: Reference Paths
audience: runtime
status: stable
last_updated: 2026-05-11
inject_by: inline (skills/setup/SKILL.md, skills/new-track/SKILL.md)
related:
  - ../skills/setup/SKILL.md
  - ../skills/new-track/SKILL.md
---

## Reference Layer

### File Resolution

#### Index-Based Resolution

- Project index: `conductor/index.md`
- Track index: `<track_dir>/index.md`

#### Default Paths

| Document           | Path                                       |
| ------------------ | ------------------------------------------ |
| Product Definition | `conductor/overview/product.md`            |
| Product Guidelines | `conductor/overview/product-guidelines.md` |
| Tech Stack         | `conductor/design/tech-stack.md`           |
| Tracks Registry    | `conductor/tracks.md`                      |
| Workflow Index     | `conductor/workflow/index.md`              |
| Code Style Guides  | `conductor/workflow/code-styleguides/`     |

#### Workflow Protocols

Resolve via `conductor/workflow/index.md`. Key files:

| Protocol           | Path                                     | Purpose                                    |
| ------------------ | ---------------------------------------- | ------------------------------------------ |
| Task Workflow      | `conductor/workflow/task-workflow.md`    | 11-step TDD workflow with selection rules  |
| Phase Checkpoint   | `conductor/workflow/phase-checkpoint.md` | Phase completion verification protocol     |
| Workflow Template  | `conductor/workflow/template.md`         | Quality standards, dev commands, guidelines |
