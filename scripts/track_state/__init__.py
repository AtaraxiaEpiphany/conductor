"""track-state package — Conductor track state management.

Lazy by design (PEP 562 ``__getattr__``): the dispatch hooks
function-level-import single submodules on EVERY subagent start/stop, and the
widened matcherless hooks fire for built-in agents too — the unrostered
fast-no-op path is the hot path. An eager ``__init__`` here made each fire pay
for the whole package (``cli`` → ``shape_studio`` → ``http.server``, …) before
the requested submodule was resolved (~200 ms marginal per hook spawn; the
Phase D no-op measurement caught it). The names below re-export exactly what
the eager ``__init__`` used to — they bind on first attribute access instead
of at package import. New code should prefer the explicit submodule import
(``from track_state import misc`` / ``from track_state.misc import cmd_reset``).
"""

from importlib import import_module

_EXPORTS = {
    "cmd_lock": "mutations", "cmd_fail": "mutations", "cmd_skip": "mutations",
    "cmd_block": "mutations", "cmd_defer": "mutations",
    "cmd_complete": "cmd_complete",
    "cmd_next": "dispatch", "cmd_dispatch_next": "dispatch",
    "cmd_dispatch_prepare": "dispatch", "cmd_dispatch_finalize": "dispatch",
    "cmd_recover": "dispatch",
    "cmd_process_result": "result", "cmd_write_result": "result",
    "cmd_validate": "validate",
    "cmd_start": "quality", "cmd_finalize": "quality", "cmd_archive": "quality",
    "cmd_gc": "quality", "cmd_checklist_verify": "quality",
    "cmd_reset": "misc", "cmd_indices": "misc", "cmd_shas": "misc",
    "cmd_add_checkpoint": "misc", "cmd_deferred_report": "misc",
    "cmd_phase_done": "misc", "cmd_registry_update": "misc",
    "cmd_record_summary": "misc",
    "cmd_get_handoff": "handoff", "cmd_sync_handoff": "handoff",
    "cmd_append_handoff": "handoff", "cmd_compile_track_findings": "handoff",
    "cmd_sync_plan": "sync",
    "main": "cli",
}


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{target}", __name__), name)


__all__ = list(_EXPORTS)
