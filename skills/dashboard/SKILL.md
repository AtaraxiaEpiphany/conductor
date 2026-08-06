---
name: dashboard
description: Live resolved-workflow dashboard — renders the track's resolved shape (nodes, checkpoint verifier fan-out, gates) with the current position, the task tree, and quality gauges. Read-only in-chat snapshot.
when_to_use: User wants to SEE the workflow (not just task status) — the resolved DAG, which gates are on, what verifiers fan out at the checkpoint, and where the active task sits in the spine. Use to make the data-driven workflow obvious. Distinct from /conductor:status (runtime task aggregation, blind to shape/gates/verifiers).
argument-hint: "[track_name]"
allowed-tools: Bash, Read
model: haiku
---

# Conductor Dashboard

You are a **thin renderer**. You do NOT parse `track-state.json`, compute status, or reason about the workflow — `track-state view --render` does all of that in code and prints the snapshot. Your entire job: resolve the track, run the render, relay it verbatim. The dashboard is a **projection** of state, never a second state machine.

## 1.0 RESOLVE THE TRACK (once)

```bash
track-state check "$ARGUMENTS"
```

Always exits 0; the outcome is in `action`:
- `proceed` → `<td>` is the `td` field; continue to §2.0.
- `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
- `halt` → print `message`; STOP.

## 2.0 RENDER (the whole skill)

```bash
track-state view "<td>" --render
```

Print the output **verbatim**. Then STOP. Do not edit it, summarize it, or add commentary — the render IS the answer. (For a machine-readable envelope instead of the Unicode snapshot, drop `--render`.)

## Notes

- Read-only: no `track-state.json` mutation, no dispatch, no firewall exposure.
- The snapshot is frozen at this turn (the host is turn-driven); re-run `/conductor:dashboard` to refresh.
- `--render` renders from the live registries, so a project overlay shape/gate set shows up with zero skill changes.
