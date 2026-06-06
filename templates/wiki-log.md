# Documentation Log

> Append-only chronological record of documentation changes.
> Parseable with standard unix tools: `grep`, `awk`, `tail`.
>
> Format: pipe-delimited table rows.
> Filter examples:
>   `grep "track-name" conductor/log.md`
>   `awk -F'|' '$1 > "2026-05-30" {print}' conductor/log.md`
>   `grep "tech-stack" conductor/log.md`

## Entries

| Timestamp | Track | Operation | Files | Summary |
|-----------|-------|-----------|-------|---------|
