---
title: Documentation Reorganization Summary
audience: developer
status: draft
last_updated: 2026-05-11
---

# Documentation Reorganization (2026-05-11)

## Overview

Reorganized Conductor plugin documentation based on audience and purpose, following industry best practices for technical documentation structure.

## Changes Made

### Directory Structure

**Before:**
```
conductor-plugin/
├── docs/                    # Mixed user and reference docs
├── reference/               # Reference docs
├── internal/                # Runtime injection docs (ambiguous naming)
└── architecture/            # Architecture docs
```

**After:**
```
conductor-plugin/
├── docs/
│   ├── user/                # 👤 User-facing documentation
│   └── reference/           # 📖 Reference manuals
├── developer/               # 🛠️ Developer documentation
│   ├── architecture/        # System architecture
│   └── guides/              # How-to guides
└── runtime/                 # 🤖 Runtime-injected documentation
```

### File Moves

| Old Path | New Path | Type |
|----------|----------|------|
| `internal/conductor-core.md` | `runtime/core-contract.md` | Runtime injection |
| `internal/conductor-orchestration.md` | `runtime/orchestration-spec.md` | Runtime reference |
| `internal/conductor-reference.md` | `runtime/reference-paths.md` | Runtime reference |
| `internal/hooks-implementation.md` | `developer/guides/extending-hooks.md` | Developer guide |
| `architecture/*.md` | `developer/architecture/*.md` | Developer docs |
| `docs/*.md` | `docs/user/*.md` | User docs |
| `reference/*.md` | `docs/reference/*.md` | Reference docs |
| `architecture/INTERACTION_REFERENCE.md` | `docs/reference/INTERACTION_REFERENCE.md` | Reference |

### Path Updates

Updated all path references in:
- `scripts/session-start.py` - `internal/conductor-core.md` → `runtime/core-contract.md`
- `scripts/test-all.py` - `internal/conductor-core.md` → `runtime/core-contract.md`
- `agents/task-executor.md` - `internal/conductor-core.md` → `runtime/core-contract.md`
- `developer/guides/extending-hooks.md` - `internal/conductor-core.md` → `runtime/core-contract.md`
- `docs/user/*.md` - `../architecture/` → `../developer/architecture/`
- `docs/reference/*.md` - `../architecture/` → `../developer/architecture/`
- `INDEX.md` - Complete restructure
- `README.md` - Updated comments

### Frontmatter Added

All documentation files now include standard frontmatter:

```yaml
---
title: Document Title
audience: user|developer|reference|runtime
status: stable|draft|deprecated
last_updated: 2026-05-11
related:
  - path/to/related-doc.md
---
```

## Benefits

1. **Clearer Intent**: Directory names now reflect their audience
2. **Easier Navigation**: Users find content faster
3. **Better Maintenance**: Developers know where to contribute
4. **Single Truth Source**: Runtime docs explicitly marked as such
5. **Scalability**: Structure supports future growth

## Migration Notes

- **Git History**: All moves tracked with `git mv` equivalent
- **No Content Loss**: All files preserved, only locations changed
- **Backward Compatibility**: Runtime paths updated in scripts

## Next Steps

- [ ] Add `docs/developer/api/` for internal API documentation
- [ ] Create `docs/developer/contributing/` for contribution guidelines
- [ ] Set up automated link checking
- [ ] Add documentation generation script for INDEX.md
- [ ] Consider static site generator (Docusaurus/MkDocs)
