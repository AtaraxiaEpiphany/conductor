"""Track-2 persona binding — the registry groundwork (commit 1).

The schema layer before the dispatch chokepoint lands: a tag row may declare
``agent: <roster-name>`` (the executor PERSONA — a rostered wrapper agent that
class's ``[Tag]`` tasks dispatch instead of task-executor). These tests pin:

- ``agent_for`` — the leading-tag accessor: declared binding returned, absent
  / untagged / malformed fail-open to ``None``;
- validation — fragment-level string-ness (``validate_tag_row``) + the
  merged-level roster-membership cross-check (``validate_merged_task_types``:
  a project tag may bind a PROJECT wrapper agent, so membership needs the
  resolved roster — the probes-precedent lazy join);
- ``roster add`` scaffold parity — ``retry``/``registry_injection`` written
  EXPLICITLY both ways (always-write-explicitly doctrine);
- ``tag add --agent`` — the binding lands on the row, unrostered bindings
  refused at the merged gate;
- studio parity — the ``agent`` dropdown (merged roster names), the per-task
  profile/card fields, and the task-graph ``route_agent`` persona arm
  (executor-only: explore/manual still route their own way);
- ``misc._roster_lint_findings`` — the check-time join catches a binding whose
  roster row drifted away after the save.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase


def _clear_registry_caches():
    """Clear BOTH module identities of each registry loader.

    The hook scripts import ``track_state.X`` (``scripts/`` on sys.path) while
    these tests import ``scripts.track_state.X`` — two module objects with
    separate lru_caches. A stale one of the pair is exactly the bug shape the
    installed-plugin incident taught (registry does NOT refresh mid-process),
    so every env flip clears both.
    """
    for modname in ("track_state.agent_roster", "track_state.task_profiles",
                    "scripts.track_state.agent_roster",
                    "scripts.track_state.task_profiles"):
        mod = sys.modules.get(modname)
        if mod is not None:
            mod._load.cache_clear()


class _ProjectEnv(TestCase):
    """Env-snapshot discipline (mirror of test_grounding_resolution): snapshot
    /restore ``CLAUDE_PROJECT_DIR`` + cache_clear on BOTH registry loaders —
    profiles and roster (the persona seam joins the two)."""

    def setUp(self):
        from scripts.track_state import task_profiles, agent_roster
        self.tp = task_profiles
        self.ar = agent_roster
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        _clear_registry_caches()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _set_project(self, proj):
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        _clear_registry_caches()

    def _write_profiles_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )
        _clear_registry_caches()

    def _write_roster_overlay(self, proj, agents):
        Path(proj, "conductor", "workflow", "agent-roster.json").write_text(
            json.dumps({"agents": agents}), encoding="utf-8",
        )
        _clear_registry_caches()


class AgentForTests(_ProjectEnv):
    """``agent_for``: the leading-tag persona lookup, fail-open."""

    def test_absent_binding_and_untagged_fail_open_to_none(self):
        # Shipped baseline carries no agent binding on any row.
        self.assertIsNone(self.tp.agent_for([]))
        self.assertIsNone(self.tp.agent_for(["Docs"]))
        self.assertIsNone(self.tp.agent_for(["Refactor"]))

    def test_declared_binding_returned_for_leading_tag(self):
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        self._set_project(proj)
        self.assertEqual(self.tp.agent_for(["Data"]), "data-plumber")
        # Leading tag only — the class-declared field is single-sourced
        # (mirrors the route).
        self.assertIsNone(self.tp.agent_for(["Docs", "Data"]))
        self.assertEqual(self.tp.agent_for(["Data", "Docs"]), "data-plumber")

    def test_malformed_binding_fails_open_to_none(self):
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": 123,
        }}})
        self._set_project(proj)
        self.assertIsNone(self.tp.agent_for(["Data"]),
                          "a malformed value must degrade to default "
                          "dispatch, never crash one")


class TagRowAgentValidation(TestCase):
    """Fragment-level: string-ness only (a project tag may bind a project
    wrapper the fragment view cannot see — membership is the merged gate)."""

    def setUp(self):
        from scripts.track_state.registry_validate import validate_tag_row
        self.validate = validate_tag_row

    def test_valid_string_binding_passes(self):
        row = {"gates": ["checkpoint"], "grounding": "data-check",
               "agent": "data-plumber"}
        self.assertEqual(self.validate("Data", row), [])

    def test_non_string_binding_refused(self):
        row = {"gates": ["checkpoint"], "grounding": "data-check",
               "agent": 123}
        errs = self.validate("Data", row)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("agent must be a non-empty string", errs[0])

    def test_empty_string_binding_refused(self):
        row = {"gates": ["checkpoint"], "grounding": "data-check",
               "agent": ""}
        errs = self.validate("Data", row)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("agent must be a non-empty string", errs[0])


class MergedMembershipTests(_ProjectEnv):
    """The save-gate cross-check: a tag's ``agent`` must name a merged-roster
    row (baseline ⊕ project overlay)."""

    ROW = {"route": "executor", "when_to_use": "pipeline work",
           "gates": ["checkpoint"], "grounding": "data-check"}

    def _merged(self, agent):
        from scripts.track_state.registry_validate import (
            validate_merged_task_types)
        row = {**self.ROW, **({"agent": agent} if agent is not None else {})}
        return validate_merged_task_types({
            "default": {"route": "executor", "gates": ["tdd", "coverage",
                                                       "checkpoint"],
                        "grounding": "test"},
            "tags": {"Data": row},
        })

    def test_unrostered_binding_fails_the_merged_gate(self):
        errs = self._merged("ghost-agent")
        self.assertEqual(len([e for e in errs if "agent binding" in e]), 1, errs)
        self.assertIn("'ghost-agent' is not in the merged agent roster", errs[0])
        self.assertIn("fail-opens to task-executor", errs[0])

    def test_absent_binding_passes(self):
        self.assertEqual(self._merged(None), [])

    def test_baseline_rostered_binding_passes(self):
        self.assertEqual(self._merged("corpus-writer"), [])

    def test_project_wrapper_binding_passes_when_rostered(self):
        # The reason membership lives at the MERGED level: a project tag may
        # bind a project wrapper agent, invisible to the plugin baseline.
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": {
            "class": "executor", "fence": "---TASK RESULT--- ... ---END RESULT---",
            "registry_injection": True, "retry": True,
        }})
        self._set_project(proj)
        self.assertEqual(self._merged("data-plumber"), [])


class RosterAddParityTests(TestCase):
    """``roster add`` writes the scaffold-parity booleans EXPLICITLY both ways
    (absent reads mean OFF — a generated row must never leave them implicit)."""

    def setUp(self):
        from scripts.track_state import agent_roster
        self.ar = agent_roster
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        _clear_registry_caches()

    def _project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        return proj

    def _overlay_row(self, proj, name):
        doc = json.loads(
            Path(proj, "conductor", "workflow", "agent-roster.json")
            .read_text(encoding="utf-8"))
        return doc["agents"][name]

    def test_defaults_written_explicitly_true(self):
        proj = self._project()
        res = self.ar.roster_add("data-plumber", "data-pipeline",
                                 project_dir=proj)
        self.assertTrue(res["ok"], res)
        row = self._overlay_row(proj, "data-plumber")
        self.assertIs(row["registry_injection"], True,
                      "explicit, not implicit-by-absence")
        self.assertIs(row["retry"], True)

    def test_opt_outs_written_explicitly_false(self):
        proj = self._project()
        res = self.ar.roster_add("fast-one", "data-pipeline",
                                 retry=False, registry_injection=False,
                                 project_dir=proj)
        self.assertTrue(res["ok"], res)
        row = self._overlay_row(proj, "fast-one")
        self.assertIs(row["registry_injection"], False)
        self.assertIs(row["retry"], False)


class TagAddAgentTests(_ProjectEnv):
    """``tag add --agent``: binding lands on the row; the merged gate refuses
    unrostered names at save time."""

    def _add(self, proj, agent=None):
        kwargs = {} if agent is None else {"agent": agent}
        return self.tp.tag_add(
            "Data", when_to_use="pipeline work", gates=["checkpoint"],
            grounding="data-check", project_dir=proj, **kwargs)

    def test_binding_written_on_the_row(self):
        proj = self._mk_project()
        # Roster the wrapper first (the merged gate reads the env ladder, so
        # the project must be current BEFORE the add).
        self._write_roster_overlay(proj, {"data-plumber": {
            "class": "executor", "fence": "---TASK RESULT--- ... ---END RESULT---",
            "registry_injection": True, "retry": True,
        }})
        self._set_project(proj)
        res = self._add(proj, agent="data-plumber")
        self.assertTrue(res["ok"], res)
        doc = json.loads(
            Path(proj, "conductor", "workflow", "task-type-profiles.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(doc["tags"]["Data"]["agent"], "data-plumber")

    def test_absent_agent_leaves_field_off_the_row(self):
        proj = self._mk_project()
        self._set_project(proj)
        res = self._add(proj)
        self.assertTrue(res["ok"], res)
        doc = json.loads(
            Path(proj, "conductor", "workflow", "task-type-profiles.json")
            .read_text(encoding="utf-8"))
        self.assertNotIn("agent", doc["tags"]["Data"],
                         "absent = default task-executor dispatch")

    def test_unrostered_binding_refused_at_save(self):
        proj = self._mk_project()
        self._set_project(proj)
        res = self._add(proj, agent="ghost-agent")
        self.assertFalse(res["ok"])
        self.assertTrue(any("not in the merged agent roster" in e
                            for e in res["errors"]), res["errors"])
        # Refused BEFORE the write — no overlay row landed.
        self.assertFalse(
            Path(proj, "conductor", "workflow", "task-type-profiles.json")
            .exists())


class StudioVocabTests(_ProjectEnv):
    """The ``agent`` dropdown sources from the merged roster (cross-module
    read, the probes precedent) — a project wrapper appears as an option."""

    def test_vocab_agent_options_follow_merged_roster(self):
        from scripts.track_state import shape_studio as ss
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": {
            "class": "executor", "fence": "---TASK RESULT--- ... ---END RESULT---",
            "registry_injection": True, "retry": True,
        }})
        self._set_project(proj)
        options = ss._vocab()["task-types"]["scalar_fields"]["agent"]
        self.assertIn("task-executor", options)
        self.assertIn("data-plumber", options)

    def test_task_profile_carries_agent_field(self):
        from scripts.track_state import shape_studio as ss
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        self._set_project(proj)
        prof = ss._task_profile("Data")
        self.assertEqual(prof["agent"], "data-plumber")
        self.assertIsNone(ss._task_profile("Docs")["agent"])

    def test_task_card_carries_agent_and_wrapped_skill(self):
        from scripts.track_state import shape_studio as ss
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        # The wrapper file wrapper_skill_for reads (project home first).
        agents_dir = Path(proj, ".claude", "agents")
        agents_dir.mkdir(parents=True)
        (agents_dir / "data-plumber.md").write_text(
            "---\nskills: [data-pipeline]\n---\nbody\n", encoding="utf-8")
        self._set_project(proj)
        card = ss._task_card(1, {"name": "[Data] Pump the pipeline",
                                 "index": 1, "status": "pending"})
        self.assertEqual(card["agent"], "data-plumber")
        self.assertEqual(card["agent_skill"], "data-pipeline")


class TaskGraphPersonaTests(TestCase):
    """``/api/task-workflow``'s ``route_agent``: a class-bound persona
    overrides the executor mapping; explore/manual route their own way."""

    def setUp(self):
        from scripts.track_state import task_profiles, agent_roster
        from scripts.track_state.core import save
        from test_shape_studio_server import _Server, _get_json
        self._save = save
        self._Server = _Server
        self._get_json = _get_json
        self.tp, self.ar = task_profiles, agent_roster
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        _clear_registry_caches()

    def _project(self, tasks, profiles_overlay):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        tdir = Path(tmp, "conductor", "tracks", "auth")
        tdir.mkdir(parents=True)
        Path(tmp, "conductor", "workflow").mkdir(parents=True)
        self._save(str(tdir), {
            "track_id": "auth", "type": "feature", "status": "in_progress",
            "workflow_shape": "default", "current_phase_index": 1,
            "current_task_index": 1,
            "phases": [{"name": "Phase 1", "status": "in_progress",
                        "tasks": tasks}],
        })
        Path(tmp, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps(profiles_overlay), encoding="utf-8")
        Path(tmp, "conductor", "workflow", "agent-roster.json").write_text(
            json.dumps({"agents": {"data-plumber": {
                "class": "executor",
                "fence": "---TASK RESULT--- ... ---END RESULT---",
                "registry_injection": True, "retry": True}}}),
            encoding="utf-8")
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        _clear_registry_caches()
        return tmp, tdir

    _DATA_ROW = {"route": "executor", "when_to_use": "pipeline work",
                 "gates": ["checkpoint"], "grounding": "data-check",
                 "agent": "data-plumber"}
    _EXPLORE_ROW = {"route": "explore", "when_to_use": "read-only look",
                    "gates": ["coverage", "checkpoint"], "grounding": "review",
                    "agent": "data-plumber"}

    def test_persona_overrides_executor_mapping(self):
        tmp, tdir = self._project(
            tasks=[{"name": "[Data] Pump the pipeline", "status": "pending"}],
            profiles_overlay={"tags": {"Data": self._DATA_ROW}})
        with self._Server(tmp) as srv:
            status, g = self._get_json(
                srv.base, f"/api/task-workflow?track={tdir}&phase=1&task=1")
            self.assertEqual(status, 200, g)
            self.assertTrue(g["ok"], g)
            self.assertEqual(g["route_agent"], "data-plumber")
            self.assertEqual(g["card"]["agent"], "data-plumber")

    def test_explore_route_ignores_persona_binding(self):
        # A persona IS an executor — non-executor routes keep their mapping
        # even when the class row carries a binding.
        tmp, tdir = self._project(
            tasks=[{"name": "[Map] the pipeline", "status": "pending"}],
            profiles_overlay={"tags": {"Map": self._EXPLORE_ROW}})
        with self._Server(tmp) as srv:
            status, g = self._get_json(
                srv.base, f"/api/task-workflow?track={tdir}&phase=1&task=1")
            self.assertEqual(status, 200, g)
            self.assertEqual(g["route_agent"], "explorer")

    def test_unbound_executor_task_still_routes_task_executor(self):
        tmp, tdir = self._project(
            tasks=[{"name": "Plain feature work", "status": "pending"}],
            profiles_overlay={"tags": {"Data": self._DATA_ROW}})
        with self._Server(tmp) as srv:
            status, g = self._get_json(
                srv.base, f"/api/task-workflow?track={tdir}&phase=1&task=1")
            self.assertEqual(status, 200, g)
            self.assertEqual(g["route_agent"], "task-executor")


class RosterLintJoinTests(_ProjectEnv):
    """``misc._roster_lint_findings``: the check-time join — a binding whose
    roster row drifted away after the save gets loud here."""

    def _overlay(self, agent):
        proj = self._mk_project()
        row = {"route": "executor", "when_to_use": "pipeline work",
               "gates": ["checkpoint"], "grounding": "data-check"}
        if agent is not None:
            row["agent"] = agent
        self._write_profiles_overlay(proj, {"tags": {"Data": row}})
        self._set_project(proj)
        return proj

    def test_dead_binding_surfaced(self):
        self._overlay("ghost-agent")
        from scripts.track_state import misc
        findings = misc._roster_lint_findings()
        hits = [f for f in findings if "ghost-agent" in f]
        self.assertEqual(len(hits), 1, findings)
        self.assertIn("[Data] → ghost-agent", hits[0])
        self.assertIn("persona", hits[0])

    def test_rostered_binding_silent(self):
        # A baseline rostered name with a shipped agent file — no finding of
        # either family (membership + declared-names both clean).
        self._overlay("corpus-writer")
        from scripts.track_state import misc
        findings = misc._roster_lint_findings()
        self.assertFalse([f for f in findings if "persona" in f
                          or "corpus-writer" in f], findings)

    def test_absent_binding_silent(self):
        self._overlay(None)
        from scripts.track_state import misc
        findings = misc._roster_lint_findings()
        self.assertFalse([f for f in findings if "persona" in f], findings)


_PERSONA_ROW = {"class": "executor",
                "fence": "---TASK RESULT--- ... ---END RESULT---",
                "registry_injection": True, "retry": True}


def _hook_module():
    """Load on-subagent-start.py as a module (the test_on_subagent_start
    pattern), on first use."""
    import importlib.util
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "on_subagent_start_persona", scripts / "on-subagent-start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ExecutorSlotTests(_ProjectEnv):
    """``executor_slot``: which dispatched agents occupy the task-executor
    slot (executor-class roster rows that are not themselves spine nodes)."""

    def test_spine_and_non_executor_agents_own_their_names(self):
        # task-executor IS the slot; explorer is executor-class but owns its
        # own spine slot; phase-checker is a spine node; code-reviewer/doc-probe
        # are reviewer/advisory class.
        self.assertIsNone(self.ar.executor_slot("task-executor"))
        self.assertIsNone(self.ar.executor_slot("explorer"))
        self.assertIsNone(self.ar.executor_slot("phase-checker"))
        self.assertIsNone(self.ar.executor_slot("code-reviewer"))
        self.assertIsNone(self.ar.executor_slot("doc-probe"))
        self.assertIsNone(self.ar.executor_slot("ghost-agent"), "unrostered")
        self.assertIsNone(self.ar.executor_slot(""))

    def test_executor_class_wrapper_occupies_the_slot(self):
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        self.assertEqual(self.ar.executor_slot("data-plumber"), "task-executor")
        # Namespaced dispatch names resolve (the canonical_name contract).
        self.assertEqual(self.ar.executor_slot("conductor:data-plumber"),
                         "task-executor")


class DispatchChokepointTests(_ProjectEnv):
    """``_build_executor`` — the single agent-name choice site for task
    dispatch: the class-bound persona wins, failing open to task-executor."""

    def _persona_project(self):
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        return proj

    def test_persona_dispatched_with_manifest_and_attempt(self):
        from scripts.track_state import dispatch as dz
        self._persona_project()
        pre = dict(phase=1, task=1, name="[Data] Pump", tags=["Data"],
                   max_retries=3)
        agent, body = dz._build_executor("/tmp/t", pre, attempt=2)
        self.assertEqual(agent, "data-plumber")
        self.assertIn("WORKFLOW_FILE=", body,
                      "the persona's procedure rides the manifest")
        self.assertIn("ATTEMPT=2", body)
        self.assertIn("MAX_RETRIES=3", body)

    def test_unbound_class_fails_open_to_task_executor(self):
        from scripts.track_state import dispatch as dz
        proj = self._mk_project()
        self._set_project(proj)
        agent, _ = dz._build_executor(
            "/tmp/t", dict(phase=1, task=1, name="x", tags=["Docs"]), 1)
        self.assertEqual(agent, "task-executor")

    def test_explore_arm_unchanged(self):
        from scripts.track_state import dispatch as dz
        proj = self._mk_project()
        self._set_project(proj)
        agent, body = dz._build_executor(
            "/tmp/t", dict(phase=1, task=1, name="[Explore] x",
                           tags=["Explore"]), 1)
        self.assertEqual(agent, "explorer")
        self.assertNotIn("ATTEMPT=", body)


class ShapeAllowsSlotTests(_ProjectEnv):
    """``shape_allows`` counts a persona against the task-executor SLOT, not
    its own name — a persona dispatch never trips a spurious violation."""

    def test_persona_occupies_the_executor_slot(self):
        from scripts.track_state.dispatch import shape_allows
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        allowed, shape = shape_allows(
            "/td", "data-plumber", state={"workflow_shape": "default"})
        self.assertTrue(allowed)
        self.assertEqual(shape, "default")

    def test_unrostered_name_still_not_admitted(self):
        # Fail-open must not over-admit: a name with no roster row keeps its
        # own name and fails the nodes membership on a default shape.
        from scripts.track_state.dispatch import shape_allows
        proj = self._mk_project()
        self._set_project(proj)
        allowed, _ = shape_allows(
            "/td", "ghost-agent", state={"workflow_shape": "default"})
        self.assertFalse(allowed)


class RegistryContextPersonaTests(_ProjectEnv):
    """``_registry_context``: a persona with ``registry_injection: true``
    passes the membership gate and must land the EXECUTOR block (its class's
    resolved profile + gate sets) — not fall through to None."""

    @classmethod
    def setUpClass(cls):
        cls.hook = _hook_module()

    def test_persona_gets_executor_registry_block(self):
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        block = self.hook._registry_context("data-plumber", proj)
        self.assertIsNotNone(block)
        self.assertIn("RESOLVED GATE SETS", block)

    def test_persona_without_injection_flag_gets_none(self):
        # registry_injection: false keeps the agent OUT of registry_agents —
        # the membership gate (not the persona arm) decides.
        proj = self._mk_project()
        row = dict(_PERSONA_ROW, registry_injection=False)
        self._write_roster_overlay(proj, {"data-plumber": row})
        self._set_project(proj)
        self.assertIsNone(self.hook._registry_context("data-plumber", proj))

    def test_explorer_still_gets_none(self):
        proj = self._mk_project()
        self._set_project(proj)
        self.assertIsNone(self.hook._registry_context("explorer", proj))


class TripwireResetSlotTests(_ProjectEnv):
    """``_reset_tripwire_counter`` is slot-aware: a persona dispatch resets the
    round counter exactly as a task-executor dispatch does (otherwise every
    persona retry starts from a stale count and trips early)."""

    @classmethod
    def setUpClass(cls):
        cls.hook = _hook_module()

    def _locked_project(self, agent_name):
        from scripts.track_state.core import save
        proj = self._mk_project()
        tdir = Path(proj, "conductor", "tracks", "auth")
        tdir.mkdir(parents=True)
        save(str(tdir), {
            "track_id": "auth", "type": "feature", "status": "in_progress",
            "workflow_shape": "default", "current_phase_index": 1,
            "current_task_index": 1,
            "phases": [{"name": "Phase 1", "status": "in_progress",
                        "tasks": [{"name": "[Data] Pump", "status":
                                   "in_progress"}]}],
        })
        trip = tdir / ".conductor" / ".tripwire-1-1.count"
        trip.parent.mkdir(parents=True, exist_ok=True)
        trip.write_text("5", encoding="utf-8")
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        return proj, trip

    def test_reset_fires_for_persona(self):
        proj, trip = self._locked_project("data-plumber")
        self.hook._reset_tripwire_counter(proj, "data-plumber")
        self.assertFalse(trip.exists())

    def test_reset_skips_explorer(self):
        proj, trip = self._locked_project("explorer")
        self.hook._reset_tripwire_counter(proj, "explorer")
        self.assertTrue(trip.exists())


class RedispatchTelemetryTests(_ProjectEnv):
    """The re-dispatch lifecycle event names the agent that actually
    re-dispatches — the persona, not the hardcoded default slot."""

    def _emit(self, tags):
        from scripts.track_state import dispatch as dz
        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        self._set_project(proj)
        dz._emit_redispatch_telemetry(str(Path(proj, "no-track")), 1, 1, None,
                                      tags)
        return Path(proj, ".conductor", "logs",
                    "dispatch-lifecycle.log").read_text(encoding="utf-8")

    def test_event_names_the_persona(self):
        log = self._emit(["Data"])
        self.assertIn("event=re-dispatch", log)
        self.assertIn("agent=data-plumber", log)

    def test_untagged_event_names_task_executor(self):
        log = self._emit([])
        self.assertIn("agent=task-executor", log)


class _HookProcessEnv(_ProjectEnv):
    """Shared fixture for the two PreToolUse hooks that scope on the executor
    slot: subprocess-run with an explicit env carrying the roster overlay."""

    def _run_hook(self, hook_name, payload, proj):
        import json as _json
        import subprocess
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = proj
        proc = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent.parent / "scripts"
                 / hook_name)],
            input=_json.dumps(payload), capture_output=True, text=True,
            env=env)
        out = _json.loads(proc.stdout) if proc.stdout.strip() else {}
        return out

    @staticmethod
    def _decision(out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision")

    @staticmethod
    def _context(out):
        return out.get("hookSpecificOutput", {}).get("additionalContext") or ""

    def _write_locked_track(self, proj):
        from scripts.track_state.core import save
        tdir = Path(proj, "conductor", "tracks", "auth")
        tdir.mkdir(parents=True)
        save(str(tdir), {
            "track_id": "auth", "type": "feature", "status": "in_progress",
            "workflow_shape": "default", "current_phase_index": 1,
            "current_task_index": 1,
            "phases": [{"name": "Phase 1", "status": "in_progress",
                        "tasks": [{"name": "[Data] Pump", "status":
                                   "in_progress"}]}],
        })
        return tdir

    def _run_hook(self, hook_name, payload, proj):
        import json as _json
        import subprocess
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = proj
        proc = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent.parent / "scripts"
                 / hook_name)],
            input=_json.dumps(payload), capture_output=True, text=True,
            env=env)
        out = _json.loads(proc.stdout) if proc.stdout.strip() else {}
        return out


class TripwireHookPersonaTests(_HookProcessEnv):
    """on-pre-tool-tripwire counts a persona's rounds — the worst T2 gap: a
    persona bound via `agent` previously escaped the round-count tripwire
    ENTIRELY (counter never bumped, shutdown directive never injected)."""

    def _count_file(self, tdir):
        return tdir / ".conductor" / ".tripwire-1-1.count"

    def test_persona_rounds_counted_and_directive_injected(self):
        proj = self._mk_project()
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        tdir = self._write_locked_track(proj)
        trip = self._count_file(tdir)
        trip.parent.mkdir(parents=True, exist_ok=True)
        trip.write_text("37", encoding="utf-8")  # one round below the wire
        out = self._run_hook("on-pre-tool-tripwire.py", {
            "tool_name": "Bash", "cwd": proj, "agent_type": "data-plumber",
            "tool_input": {"command": "echo hi"},
        }, proj)
        self.assertEqual(trip.read_text().strip(), "38",
                         "the persona's round must bump the SAME counter")
        self.assertEqual(self._decision(out), "allow")
        self.assertIn("CONDUCTOR TRIPWIRE", self._context(out),
                      "at the wire the shutdown directive injects")

    def test_explorer_rounds_still_no_op(self):
        proj = self._mk_project()
        self._set_project(proj)
        tdir = self._write_locked_track(proj)
        out = self._run_hook("on-pre-tool-tripwire.py", {
            "tool_name": "Bash", "cwd": proj, "agent_type": "explorer",
            "tool_input": {"command": "echo hi"},
        }, proj)
        self.assertEqual(self._decision(out), "allow")
        self.assertFalse(self._count_file(tdir).exists())


class CleanTreeHookPersonaTests(_HookProcessEnv):
    """on-write-result-clean-tree gates a persona's SUCCESS claim — without
    slot-occupancy a persona bypassed the stranded-files guard."""

    def _dirty_repo(self):
        import subprocess
        proj = self._mk_project()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=proj,
                       check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=proj,
                       check=True)
        return proj

    def test_persona_success_on_dirty_tree_denied(self):
        proj = self._dirty_repo()
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)
        tdir = self._write_locked_track(proj)
        # An untracked implementation file — the stranded-files shape.
        (Path(proj) / "impl_thing.py").write_text("x = 1\n", encoding="utf-8")
        out = self._run_hook("on-write-result-clean-tree.py", {
            "tool_name": "Bash", "cwd": proj, "agent_type": "data-plumber",
            "tool_input": {"command": f'track-state write-result "{tdir}" '
                                      f'--status success --summary done'},
        }, proj)
        self.assertEqual(self._decision(out), "deny")

    def test_explorer_success_claim_untouched(self):
        proj = self._dirty_repo()
        self._set_project(proj)
        tdir = self._write_locked_track(proj)
        (Path(proj) / "impl_thing.py").write_text("x = 1\n", encoding="utf-8")
        out = self._run_hook("on-write-result-clean-tree.py", {
            "tool_name": "Bash", "cwd": proj, "agent_type": "explorer",
            "tool_input": {"command": f'track-state write-result "{tdir}" '
                                      f'--status success --summary done'},
        }, proj)
        self.assertEqual(self._decision(out), "allow")


class WrapperMaxTurnsTests(TestCase):
    """The wrapper template's maxTurns matches task-executor's 64 — with the
    tripwire active, 38-of-48 left only 10 shutdown rounds vs 26."""

    def test_template_writes_64(self):
        from scripts.track_state import agent_roster as ar
        self.assertIn("maxTurns: 64", ar._WRAPPER_TEMPLATE)
        self.assertNotIn("maxTurns: 48", ar._WRAPPER_TEMPLATE)

    def test_generated_wrapper_carries_64(self):
        from scripts.track_state import agent_roster as ar
        import tempfile
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj,
                                                            ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        res = ar.roster_add("data-plumber", "data-pipeline", project_dir=proj)
        self.assertTrue(res["ok"], res)
        text = (Path(res["agent_path"])).read_text(encoding="utf-8")
        self.assertIn("maxTurns: 64", text)


class WaveScopePinTests(_ProjectEnv):
    """The declared boundary: persona binding is SERIAL-RAIL-ONLY. Wave
    members always dispatch task-executor (skills/parallel §3.2 dispatches
    ``conductor:task-executor`` per member) — members carry no ``agent`` key
    in the envelope, the ledger, or the projection whitelist, and the member
    prompt never consults the binding. Extending waves to parallel personas
    waits for a real need (extensibility decision, not an oversight)."""

    def test_wave_members_dispatch_task_executor_regardless_of_binding(self):
        import io
        import subprocess
        from scripts.track_state.core import save
        from scripts.track_state.wave import (
            cmd_dispatch_wave, _wave_ledger_path, _wave_assemble_member_prompt,
            _ORCH_MEMBER_KEYS)

        proj = self._mk_project()
        self._write_profiles_overlay(proj, {"tags": {"Data": {
            "route": "executor", "when_to_use": "pipeline work",
            "gates": ["checkpoint"], "grounding": "data-check",
            "agent": "data-plumber",
        }}})
        self._write_roster_overlay(proj, {"data-plumber": dict(_PERSONA_ROW)})
        self._set_project(proj)

        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=proj,
                       check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=proj,
                       check=True)
        Path(proj, "README.md").write_text("# base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=proj, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=proj,
                       check=True)
        Path(proj, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n"
            "- [ ] [Data] Task 1: t1 <!-- deps: -->\n"
            "- [ ] [Data] Task 2: t2 <!-- deps: -->\n",
            encoding="utf-8")
        save(str(proj), {
            "track_id": "wtest", "type": "feature", "status": "in_progress",
            "current_phase_index": 1, "current_task_index": 0,
            "phases": [{"name": "Phase 1", "tasks": [
                {"name": "[Data] Task 1: t1", "status": "pending"},
                {"name": "[Data] Task 2: t2", "status": "pending"}]}],
        })

        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            cmd_dispatch_wave(str(proj))
            out = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(out["action"], "dispatch_wave")
        members = out["wave"]
        self.assertEqual(len(members), 2)
        for m in members:
            self.assertNotIn("agent", m,
                             "the slim consumer shape carries no persona")
        ledger = json.loads(_wave_ledger_path(str(proj)).read_text())
        self.assertEqual(len(ledger["wave"]), 2)
        for m in ledger["wave"]:
            self.assertNotIn("agent", m, "the ledger is agent-free too")
            prompt = _wave_assemble_member_prompt(m)
            self.assertNotIn("data-plumber", prompt,
                             "the pre-assembled member prompt never names "
                             "the persona — task-executor dispatches it")
        # Drift guard: the projection whitelist stays agent-free.
        self.assertNotIn("agent", _ORCH_MEMBER_KEYS)


if __name__ == "__main__":
    unittest.main()
