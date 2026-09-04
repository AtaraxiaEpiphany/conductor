"""Golden + agreement tests for the per-dispatch workflow manifest
(``track_state.dispatch_manifest`` — conductor/design/dispatch-manifest.md).

The manifest is the code-composed seam between the dispatch lifecycle and the
executor's workflow: it resolves ONCE, in code, what the executor used to
re-derive in its head from injected fragments (resolved gates ⊕ tag exemptions,
the ONE workflow path decision, pointers). Two invariant families live here:

- **Golden determinism** — ``compose_manifest`` is a pure function of
  ``(state, pre)``: byte-identical across calls, independent of the track_dir
  argument, with no timestamps and no absolute install paths baked into the
  body (docfiles are pinned by the ``${CLAUDE_PLUGIN_ROOT}`` token +
  home-relative identity, so a retry overwrite renders the same bytes and
  re-dispatch stays idempotent across plugin upgrades).
- **Floor agreement** — the injected ``[Conductor Registry]`` block
  (on-subagent-start) is the deterministic floor; the manifest is the
  per-dispatch pinning of the SAME registry resolution. The two emitters are
  deliberately separate code paths — this file pins that they cannot drift:
  for the same locked task, whatever the injected block tells the executor
  about the workflow, the manifest's Workflow path decision agrees (docfile
  ↔ docfile, inline ↔ inline, absent ↔ default-TDD, exempt ↔ fast-path).
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import dispatch as dz  # noqa: E402
from track_state import dispatch_manifest as dm  # noqa: E402
from track_state import task_profiles as tp  # noqa: E402
from track_state import wave as wave_mod  # noqa: E402
from track_state.helpers import extract_tags  # noqa: E402


def _hook():
    """Load on-subagent-start.py as a module (it has import-time side effects
    only under __main__; the pattern is shared with test_registry_injection)."""
    spec = importlib.util.spec_from_file_location(
        "oss_dm_hook", _scripts / "on-subagent-start.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def _state(name="plain impl task", status="in_progress"):
    # task_type mirrors what task_profiles.derive_task_type writes at
    # construction: the leading tag, lowercased ("default" when untagged) —
    # _resolve_locked_task_type reads THAT field, not the name.
    tags = extract_tags(name)
    tt = tags[0].lower() if tags else "default"
    return {
        "current_phase_index": 1,
        "current_task_index": 1,
        "workflow_shape": "default",
        "phases": [{"tasks": [{"name": name, "status": status,
                               "task_type": tt}]}],
    }


def _pre(name):
    """The pre-dispatch dict _find_next_task would hand compose_manifest."""
    return {"phase": 1, "task": 1, "subtask": None,
            "name": name, "tags": extract_tags(name)}


_BARE_STEPS = re.compile(
    r"(?<!\$\{CLAUDE_PLUGIN_ROOT\}/)templates/workflow/steps/[\w.-]+\.md")


class DeterminismTests(TestCase):
    """Golden: the manifest is a pure function of (state, pre)."""

    def test_compose_is_byte_stable_across_calls(self):
        body1 = dm.compose_manifest("/tmp/track-a", _state(), _pre("[Migrate] x"))
        body2 = dm.compose_manifest("/tmp/track-a", _state(), _pre("[Migrate] x"))
        self.assertEqual(body1, body2)

    def test_body_independent_of_track_dir(self):
        # track_dir names the WRITE location; it must never leak into the body
        # (byte-stability across the same state on retry, whatever the dir).
        a = dm.compose_manifest("/tmp/track-a", _state(), _pre("[Migrate] x"))
        b = dm.compose_manifest("/tmp/completely/different", _state(), _pre("[Migrate] x"))
        self.assertEqual(a, b)

    def test_no_absolute_install_paths_in_body(self):
        # The plugin's absolute install path must not be baked in — the token
        # + home-relative identity keep the body stable across installs.
        body = dm.compose_manifest("/tmp/t", _state(), _pre("[Migrate] x"))
        self.assertNotIn(str(tp._plugin_root()), body)  # noqa: SLF001
        self.assertNotIn(str(Path(tempfile.gettempdir())), body)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/", body)

    def test_every_steps_ref_in_body_is_token_prefixed(self):
        # Runtime-output mirror of test_no_dangling_workflow_doctrine's
        # _BARE_STEPS: every docfile path the manifest hands the executor must
        # be resolvable in a foreign project (token prefix, or the legitimate
        # project-relative conductor/workflow/steps/ home).
        cases = [
            _state(),                                   # default docfile
            _state("[Migrate] port the importer"),       # plugin docfile
            _state("[Docs] tweak readme"),               # fast-path
        ]
        for state in cases:
            body = dm.compose_manifest("/tmp/t", state, _pre(state["phases"][0]["tasks"][0]["name"]))
            self.assertEqual(
                _BARE_STEPS.findall(body), [],
                f"bare steps ref in manifest body: {_BARE_STEPS.findall(body)}")

    def test_write_manifest_idempotent_then_reapable(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d) / "conductor" / "tracks" / "demo"
            dm.write_manifest(td, _state(), _pre("[Migrate] x"))
            first = dm.manifest_path(td).read_text(encoding="utf-8")
            dm.write_manifest(td, _state(), _pre("[Migrate] x"))
            self.assertEqual(dm.manifest_path(td).read_text(encoding="utf-8"), first)
            dm.reap_manifest(td)
            self.assertFalse(dm.manifest_path(td).exists())
            dm.reap_manifest(td)  # missing_ok: reaping an absent file is fine

    def test_fail_open_never_raises(self):
        # Malformed state/registry degrade to the default-TDD decision —
        # dispatch must never deadlock over the manifest (mirrors
        # resolve_phase_gate's fail-open posture).
        for state, pre in [
            ({}, {"phase": 1, "task": 1, "subtask": None, "name": "?", "tags": []}),
            (_state(), {"phase": None, "task": None, "subtask": None,
                        "name": None, "tags": None}),
            ({"workflow_shape": "not-a-shape"}, _pre("[Migrate] x")),
        ]:
            body = dm.compose_manifest("/tmp/t", state, pre)
            self.assertIn("## Workflow path", body)


class PathDecisionTests(TestCase):
    """The ONE resolution this dispatch owes, per precedence:
    workflow_doc → inline `workflow` → both-exempt fast-path → default TDD."""

    def test_untagged_task_pins_default_docfile(self):
        body = dm.compose_manifest("/tmp/t", _state("plain impl task"),
                                   _pre("plain impl task"))
        self.assertIn("path: docfile `default-tdd.md`", body)
        self.assertIn("Steps 3-8", body)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/"
                      "default-tdd.md", body)

    def test_docfile_tag_pins_declared_docfile(self):
        body = dm.compose_manifest("/tmp/t", _state(), _pre(
            "[Migrate] port the legacy importer"))
        self.assertIn("path: docfile `migrate.md`", body)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/"
                      "migrate.md", body)

    def test_tdd_exempt_tag_uses_fast_path(self):
        body = dm.compose_manifest("/tmp/t", _state("[Docs] tweak readme"),
                                   _pre("[Docs] tweak readme"))
        self.assertIn("path: fast-path", body)
        self.assertIn("skip Steps 3-7", body)
        self.assertIn("- tdd_exempt: true", body)

    def test_tdd_only_exempt_tag_does_not_fast_path(self):
        # The trap the tightening closes: a tdd-only-exempt EXECUTOR tag owes
        # the 80% coverage floor, and a fast-path executor cannot see the
        # gate it fails. Baseline escapes only because its one tdd-only-exempt
        # tag (Explore) routes to explorer — so construct the case via
        # overlay, the only place it can exist.
        d = tempfile.mkdtemp()
        proj = Path(d)
        wf = proj / "conductor" / "workflow"
        wf.mkdir(parents=True)
        (wf / "task-type-profiles.json").write_text(json.dumps(
            {"tags": {"SpecDraft": {"route": "executor",
                                    "gates": ["coverage", "checkpoint"],
                                    "grounding": "test"}}}))
        self.addCleanup(shutil.rmtree, str(proj), True)
        name = "[SpecDraft] draft the acceptance spec"
        with _OverlayEnv(proj):
            body = dm.compose_manifest(str(proj), _state(name), _pre(name))
        self.assertNotIn("path: fast-path", body)
        self.assertIn("path: docfile `default-tdd.md`", body)

    def test_shape_drives_gates_independently_of_tags(self):
        # Gates come from the workflow-shape; exemptions from the tag profile;
        # the fire rule is the join. A migration shape drops tdd/coverage even
        # for a non-exempt tag.
        state = _state("plain impl task")
        state["workflow_shape"] = "migration"
        body = dm.compose_manifest("/tmp/t", state, _pre("plain impl task"))
        self.assertIn("workflow-shape: migration", body)
        self.assertIn("- gates: checkpoint", body)


class _OverlayEnv:
    """Context manager: point the registry/docfile ladder at a synthetic
    project (overlay + project steps dir) and clear task_profiles' lru_cache
    so the overlay is actually read; restore + re-clear on exit."""

    def __init__(self, proj):
        self.proj = proj
        self._prev = None

    def __enter__(self):
        self._prev = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        tp._load.cache_clear()  # noqa: SLF001
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prev
        tp._load.cache_clear()  # noqa: SLF001


class OverlayDecisionTests(TestCase):
    """Project overlays reach the manifest: an inline `workflow` tag stays on
    the legacy inline branch; a project docfile wins the ladder."""

    def _project(self, overlay_doc, steps_files=()):
        d = tempfile.mkdtemp()
        proj = Path(d)
        wf = proj / "conductor" / "workflow"
        wf.mkdir(parents=True)
        tags = {"tags": {"CustomProc": dict(overlay_doc)}}
        (wf / "task-type-profiles.json").write_text(json.dumps(tags))
        steps = wf / "steps"
        steps.mkdir()
        for fname, content in steps_files:
            (steps / fname).write_text(content)
        self.addCleanup(shutil.rmtree, str(proj), True)
        return proj

    def test_inline_workflow_tag_uses_inline_branch(self):
        proj = self._project({"workflow": "Do the custom procedure steps."})
        name = "[CustomProc] run the ritual"
        with _OverlayEnv(proj):
            body = dm.compose_manifest(str(proj), _state(name), _pre(name))
        self.assertIn("path: inline", body)
        self.assertIn("track-state registry-doc --tag CustomProc", body)

    def test_project_docfile_wins_the_ladder(self):
        proj = self._project(
            {"workflow_doc": "rollout.md"},
            steps_files=[("rollout.md", "# Rollout steps\nEXISTING projects only.\n")])
        name = "[CustomProc] run the ritual"
        with _OverlayEnv(proj):
            body = dm.compose_manifest(str(proj), _state(name), _pre(name))
        self.assertIn("path: docfile `rollout.md`", body)
        self.assertIn("read conductor/workflow/steps/rollout.md (project home wins)", body)

    def test_missing_declared_docfile_falls_back_honestly(self):
        # workflow_doc names a file no steps dir has → resolve fail-opens to
        # default-tdd; the manifest must surface the FALLBACK + SPEC_DEVIATION,
        # not point the executor at a file that doesn't exist.
        proj = self._project({"workflow_doc": "ghost.md"})
        name = "[CustomProc] run the ritual"
        with _OverlayEnv(proj):
            body = dm.compose_manifest(str(proj), _state(name), _pre(name))
        self.assertIn("`ghost.md` NOT FOUND in any steps dir", body)
        self.assertIn("SPEC_DEVIATION", body)
        self.assertIn("default-tdd.md", body)


class FloorAgreementTests(TestCase):
    """The headline invariant: the injected [Conductor Registry] block and the
    manifest are two emitters over one registry — for the same locked task they
    must agree on the workflow resolution. The hook resolves the tag from the
    locked task's name; the manifest from pre's tags; both from the same name."""

    def _injected(self, proj, name):
        track = proj / "conductor" / "tracks" / "demo"
        track.mkdir(parents=True, exist_ok=True)
        (track / "track-state.json").write_text(json.dumps(_state(name)))
        return _hook()._registry_for_executor(str(proj))  # noqa: SLF001

    def _agree(self, name, injected_must, manifest_must, injected_absent=()):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            injected = self._injected(proj, name)
            self.assertIn("[Conductor Registry]", injected)
            for needle in injected_must:
                self.assertIn(needle, injected)
            for needle in injected_absent:
                self.assertNotIn(needle, injected)
            body = dm.compose_manifest(str(proj), _state(name), _pre(name))
            for needle in manifest_must:
                self.assertIn(needle, body)
        return injected, body

    def test_docfile_tag_agrees(self):
        injected, body = self._agree(
            "[Migrate] port the legacy importer",
            ["workflow: present — docfile `migrate.md`"],
            ["path: docfile `migrate.md`"])
        # Both name the same file — the agreement, not just co-presence.
        self.assertIn("migrate.md", injected)
        self.assertIn("migrate.md", body)

    def test_untagged_task_agrees_on_default(self):
        # An untagged task resolves NO leading tag: the floor emits no profile
        # (hence no workflow line at all) and the manifest pins default TDD —
        # the agreement is "nothing bespoke" on both sides.
        self._agree(
            "plain impl task",
            ["RESOLVED EXEMPTION SETS"],
            ["path: docfile `default-tdd.md`"],
            injected_absent=["workflow:"])

    def test_exempt_tag_agrees_on_fast_path(self):
        self._agree(
            "[Docs] tweak readme",
            ["tdd_exempt: True"],
            ["path: fast-path", "- tdd_exempt: true"])

    def test_overlay_inline_tag_agrees(self):
        proj = Path(tempfile.mkdtemp())
        wf = proj / "conductor" / "workflow"
        wf.mkdir(parents=True)
        (wf / "task-type-profiles.json").write_text(json.dumps(
            {"tags": {"CustomProc": {"workflow": "Do the custom steps."}}}))
        self.addCleanup(shutil.rmtree, str(proj), True)
        name = "[CustomProc] run the ritual"
        with _OverlayEnv(proj):
            self._agree(
                name,
                ["workflow: present — run `track-state registry-doc --tag CustomProc`"],
                ["path: inline (legacy `workflow` prose on tag [CustomProc])"])


class EnvelopeWiringTests(TestCase):
    """WORKFLOW_FILE rides the executor envelope on every rail (serial
    dispatch-next, wave member prompts) — executor arm only: explorers owe no
    workflow, and no manifest is written for them. The pointer is APPENDED
    after MAX_RETRIES on both rails (one shape, no interleaving).

    FINDINGS_FILE (findings/artifact edge) rides BOTH arms and the wave member
    prompt — emitted only when the compiled track-findings doc exists (absent
    line = none recorded yet, never a dangling pointer)."""

    def test_serial_executor_envelope_carries_workflow_file(self):
        pre = _pre("[Migrate] port the legacy importer")
        agent, body = dz._build_executor("/tmp/t", pre, attempt=1)  # noqa: SLF001
        self.assertEqual(agent, "task-executor")
        self.assertIn(f"WORKFLOW_FILE={dm.manifest_path('/tmp/t')}", body)
        self.assertLess(body.index("MAX_RETRIES="), body.index("WORKFLOW_FILE="))

    def test_explorer_envelope_has_no_workflow_file(self):
        pre = _pre("[Explore] map the module")
        agent, body = dz._build_executor("/tmp/t", pre, attempt=1)  # noqa: SLF001
        self.assertEqual(agent, "explorer")
        self.assertNotIn("WORKFLOW_FILE=", body)

    def test_findings_line_absent_when_doc_missing(self):
        # /tmp/t has no .conductor/track-findings.md — the line must be omitted
        # entirely (absent line = none recorded), never a dangling pointer.
        for pre in (_pre("[Migrate] port the importer"), _pre("[Explore] map")):
            _, body = dz._build_executor("/tmp/t", pre, attempt=1)  # noqa: SLF001
            self.assertNotIn("FINDINGS_FILE=", body)

    def test_findings_line_present_on_both_arms_when_doc_exists(self):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        findings = Path(td) / ".conductor" / "track-findings.md"
        findings.parent.mkdir(parents=True)
        findings.write_text("# Track Findings\n", encoding="utf-8")
        expected = f"FINDINGS_FILE={findings}"
        # Executor arm: above ATTEMPT (before the explore early-return point).
        pre = _pre("[Migrate] port the importer")
        agent, body = dz._build_executor(td, pre, attempt=1)  # noqa: SLF001
        self.assertEqual(agent, "task-executor")
        self.assertIn(expected, body)
        self.assertLess(body.index("NAME="), body.index("FINDINGS_FILE="))
        self.assertLess(body.index("FINDINGS_FILE="), body.index("ATTEMPT="))
        # Explorer arm: the heaviest re-reader of prior findings must also get
        # the line — and still no WORKFLOW_FILE.
        pre = _pre("[Explore] map the module")
        agent, body = dz._build_executor(td, pre, attempt=1)  # noqa: SLF001
        self.assertEqual(agent, "explorer")
        self.assertIn(expected, body)
        self.assertNotIn("WORKFLOW_FILE=", body)

    def test_wave_member_prompt_carries_worktree_manifest(self):
        member = {"worktree": "/tmp/wt",
                  "worktree_track_dir": "/tmp/wt/conductor/tracks/demo",
                  "phase": 2, "task": 3, "name": "[Migrate] x"}
        body = wave_mod._wave_assemble_member_prompt(member)  # noqa: SLF001
        self.assertIn(
            f"WORKFLOW_FILE={dm.manifest_path('/tmp/wt/conductor/tracks/demo')}",
            body)
        self.assertLess(body.index("MAX_RETRIES="), body.index("WORKFLOW_FILE="))

    def test_wave_member_prompt_findings_line_mirrors_serial_contract(self):
        # The worktree findings mirror lands at prepare time; the member prompt
        # line follows the serial contract (present only when the copy exists).
        member = {"worktree": "/tmp/wt",
                  "worktree_track_dir": "/tmp/wt/conductor/tracks/demo",
                  "phase": 2, "task": 3, "name": "[Migrate] x"}
        body = wave_mod._wave_assemble_member_prompt(member)  # noqa: SLF001
        self.assertNotIn("FINDINGS_FILE=", body)  # /tmp/wt has no mirror

        wt_td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wt_td, True)
        findings = Path(wt_td) / ".conductor" / "track-findings.md"
        findings.parent.mkdir(parents=True)
        findings.write_text("# Track Findings\n", encoding="utf-8")
        member = {"worktree": "/tmp/wt", "worktree_track_dir": wt_td,
                  "phase": 2, "task": 3, "name": "[Migrate] x"}
        body = wave_mod._wave_assemble_member_prompt(member)  # noqa: SLF001
        self.assertIn(f"FINDINGS_FILE={findings}", body)


if __name__ == "__main__":
    main()
