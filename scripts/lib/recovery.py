"""Shared vocabulary for the SubagentStop recovery flow.

"Recovery" here is the SubagentStop→PostToolUse pair: when a subagent stops
without its expected artifact, the SubagentStop hook (``on-subagent-stop.py``)
injects a ``[Conductor Recovery]`` turn keeping it alive, and the PostToolUse
filter (``filter-subagent-output.py``) detects whether that recovery turn
succeeded. This module is the single source of truth for the vocabulary both
sides share — the marker prefix, the result-file agent set, and the result-
block grammar — so the two hooks cannot drift apart (they previously each
hardcoded all three, and a change on one side silently broke the other).

Distinct from ``track_state.dispatch.cmd_recover`` (state-machine *resumption*
after an orchestrator interrupt — it shares the word "recover" but is unrelated
to this flow) and ``git_ops._recover_git_notes`` (git-notes repair).
"""
import re

# The bracketed token marking a hook-injected recovery turn. on-subagent-stop
# prefixes its block-reason with this; filter-subagent-output keys its recovery
# detection on the same prefix substring. If this changes, BOTH hooks must change
# together — hence the single constant.
RECOVERY_MARKER = "[Conductor Recovery]"

# Agents whose completion is gated on a fresh ``.conductor/result.json`` (written
# by the agent, consumed by dispatch-finalize). on-subagent-stop triggers a
# recovery turn when this file is stale/missing for these agents; the PostToolUse
# filter routes a missing result block to dispatch-finalize for the SAME set.
# Defined once here so the two hooks agree on who is a "result-file agent" —
# adding one is a single-line change (Tier 2 #21 will extend this, noting those
# agents' finalize path differs).
RESULT_FILE_AGENT_TYPES = frozenset({"task-executor", "explorer"})

# The result-block grammar (e.g. ``---TASK RESULT---`` / ``---END RESULT---``,
# ``---END CHECKPOINT RESULT---``). on-subagent-stop checks an agent emitted its
# close tag; filter-subagent-output extracts the full open+close block. Both
# derive from this one definition of what a result block looks like.
#
# The class allows DIGITS as well as letters: test-runner's open tag is
# ``---L1 VERIFY RESULT---`` and the ``1`` is not in ``[A-Z ]``. A letters-only
# class made that block invisible to extract_result_blocks — every test-runner
# result was replaced with the generic no-result warning and the phase-checker
# fan-out could not parse L1_VERIFY_STATUS. Keep digits in.
#
# The ``\s+``/``\s*`` after the dashes tolerates the model emitting stray
# whitespace around the inner words — ``--- REVIEW RESULT ---`` or
# ``---END  TASK RESULT---`` otherwise fail the strict ``--- [A-Z0-9 ]+ ---``
# grammar, are treated as "no result block," and force a spurious recovery turn
# (or, on the second stop, the generic no-dispatch-finalize warning). A real
# ``---`` separator never carries inner whitespace, so this can't create a false
# positive on ordinary prose.
RESULT_END_TAG = r"---END\s+[A-Z0-9 ][A-Z0-9 ]*---"
RESULT_BLOCK_PATTERN = rf"---\s*[A-Z][A-Z0-9 ]*---.*?{RESULT_END_TAG}"

# Structured verdict: agents may emit a fenced ```json block INSIDE their
# ---RESULT--- block carrying a machine-branchable verdict object
# (``{"status": "FAILED", "failure_reason": "..."}``). This is the control-flow
# backbone upgrade — routing (the loop-back edge, the recovery branch) branches
# on ``verdict["status"]`` instead of regex-mining ``STATUS:`` prose. The fenced
# fence is tolerant of ``json``/``JSON`` and surrounding whitespace. ``.*?`` +
# non-greedy + DOTALL grabs the smallest fenced object after a result open tag.
_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_result_block(text):
    """Extract a structured verdict object from a result block, if present.

    Looks for a fenced ```` ```json ```` object emitted INSIDE a
    ``---...RESULT---`` block and returns it as a dict; returns ``None`` when
    there is no JSON object (the caller falls back to the existing regex-mined
    prose extraction). Additive and fail-open: a malformed/missing object never
    breaks extraction — the regex ``RESULT_BLOCK_PATTERN`` path still works, so
    this lands with no migration cliff (agents emit JSON when they can; prose
    when they can't; both parse).

    The contract on the returned dict is intentionally loose — only ``status``
    is assumed by routing (``passed``/``FAILED``/``error``/``warn``/…). Every
    other field (``failure_reason``, ``fix_directives``, the verify-mode
    ``report_field`` values ``BUILD``/``L1_VERIFY``/``START``/``ANCHOR``/``ADVERSARIAL``) is
    pass-through and read defensively with ``.get()``. An object missing
    ``status`` is treated as "no structured verdict" → ``None`` so a malformed
    block falls back to prose rather than routing on a missing key.
    """
    if not text:
        return None
    # Restrict the fence search to text inside a result block so a stray JSON
    # fence elsewhere in the agent's output can't be mistaken for a verdict.
    blocks = re.findall(RESULT_BLOCK_PATTERN, text, re.DOTALL)
    if not blocks:
        return None
    import json
    for block in blocks:
        m = _JSON_FENCE_PATTERN.search(block)
        if not m:
            continue
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(obj, dict) and "status" in obj:
            return obj
    return None

# Bounded recovery: how many SubagentStop recovery turns a result-file agent
# gets before the hook stops forcing them and lets dispatch-finalize synthesize
# a result (→ ``_do_fail`` retry queue). Caps a crash-looping agent at this many
# extra turns instead of burning its whole ``maxTurns`` budget before Layer-2
# synthesis engages. Counted per locked task and reset when a new task is locked
# (see ``track_state.mutations._do_lock`` / ``increment_recovery_turns``). Lives
# here alongside ``RESULT_FILE_AGENT_TYPES`` so the SubagentStop hook and the
# state-machine counter share one vocabulary.
MAX_RECOVERY_TURNS = 2
RECOVERY_TURN_FIELD = "recovery_turns"  # track-state.json key on the locked task


# --- Session-scoped recovery counter (STDOUT-block agents) -------------------
#
# Result-file agents (task-executor, explorer) bound their recovery turns on the
# LOCKED TASK (``track_state.mutations.increment_recovery_turns``) — they always
# run under a (phase, task, subtask) cursor. STDOUT-block agents (spec-reviewer,
# code-reviewer, phase-checker, ...) do NOT — they run pre-state (new-track §2.4)
# or as review/verify leaves with no task cursor to key on. A session-scoped
# sidecar gives them the same MAX_RECOVERY_TURNS escape hatch: each recovery turn
# of the SAME subagent dispatch shares its ``session_id``, so the counter bounds
# how many times the hook will force "emit your block" before letting the stop
# land (→ the agent dies honestly instead of burning its whole maxTurns budget
# on recovery turns that can never succeed — the spec-reviewer "always returns
# non-standard result" failure mode).
#
# The file lives under ``get_data_dir()`` (project-scoped, gitignored) and is
# keyed by session_id → {count, ts}. Stale entries (>SESSION_RECOVERY_TTL) are
# reaped on write so an abandoned subagent's key can't pin the file forever.
_SESSION_RECOVERY_FILE = "subagent-recovery-counters.json"
SESSION_RECOVERY_TTL = 3600  # seconds; a single subagent dispatch is far shorter


def increment_session_recovery(session_id: str) -> int:
    """Bump the recovery counter for one subagent dispatch (by ``session_id``).

    Returns the new count (≥1). Best-effort and fail-safe: any IO error returns
    ``1`` so the caller still forces one recovery turn (the prior unbounded
    behavior's floor) rather than silently allowing a no-result stop.

    A None/empty ``session_id`` (payload lacked one) returns ``1`` — without a
    stable key there is nothing to bound, so fall back to a single attempt.
    """
    if not session_id:
        return 1
    try:
        import json
        import time
        from .env import get_data_dir

        path = get_data_dir() / _SESSION_RECOVERY_FILE
        data = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, dict):
                    data = raw
            except (ValueError, OSError):
                data = {}
        # Reap stale entries on every write so the file can't grow unbounded.
        now = time.time()
        for sid in list(data):
            entry = data.get(sid)
            if isinstance(entry, dict) and isinstance(entry.get("ts"), (int, float)):
                if now - entry["ts"] > SESSION_RECOVERY_TTL:
                    del data[sid]
            else:
                del data[sid]
        count = (data.get(session_id, {}).get("count", 0) or 0) + 1
        data[session_id] = {"count": count, "ts": now}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
        return count
    except Exception:
        return 1


def clear_session_recovery(session_id: str) -> None:
    """Drop the counter for a dispatch once it emitted its block (success) — so a
    later dispatch that reuses a token (rare) starts at a fresh budget. Best-effort."""
    if not session_id:
        return
    try:
        import json
        from .env import get_data_dir

        path = get_data_dir() / _SESSION_RECOVERY_FILE
        if not path.exists():
            return
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict) or session_id not in raw:
            return
        del raw[session_id]
        path.write_text(json.dumps(raw, ensure_ascii=False))
    except Exception:
        pass
