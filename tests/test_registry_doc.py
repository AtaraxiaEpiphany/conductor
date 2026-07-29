"""Tests for ``track-state registry-doc`` — the read-only resolved-registry render.

``registry-doc`` is the **live-data view** that complements the hand-maintained
teaching tables in ``runtime/contracts/plan-format-contract.md`` (the contract
prose is richer than a registry string can hold; this render is always-current).
It renders the RESOLVED registry (plugin baseline ⊕ project overlay) as the
tag/mode tables on stdout.

Load-bearing invariants under test:

- **Read-only**: no ``track_dir`` arg, no ``track-state.json`` mutation, no
  writes anywhere. This is what lets ``registry-doc`` sit in the sanctioned
  subcommand set (a read-only render can never be the catastrophic op the broad
  rm/mv scan guards against). The test confirms no file is touched.
- **Coverage**: stdout contains every ``TAG_VOCAB()`` member AND every
  ``MODE_VOCAB()`` member — the render is a faithful view of the resolved vocab.
- **Overlay-aware**: a project overlay's tag/mode appears in the render.
- **Sanctioned**: ``registry-doc`` is in ``_SANCTIONED_TS_SUBCOMMANDS`` (else the
  pre-command-check broad-verb scan would false-positive on it).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_CLI = _scripts / "track-state"

from track_state import task_profiles as tp  # noqa: E402
from track_state import verify_mode_profiles as vmp  # noqa: E402

# pre-command-check.py is a standalone script (not under the track_state package),
# so import it as a source file rather than a module.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py")
_pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcc)


def _run_cli(env=None):
    """Run ``track-state registry-doc`` and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(_CLI), "registry-doc"],
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class RegistryDocRender(TestCase):
    """The render is read-only, exits 0, and covers every vocab member."""

    def test_exits_zero_with_clean_env(self):
        # No CLAUDE_PROJECT_DIR, no conductor/tracks/ in cwd → baseline only.
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        rc, out, err = _run_cli(env=env)
        self.assertEqual(rc, 0, f"registry-doc failed: {err}\n{out}")

    def test_renders_every_task_type(self):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        rc, out, _ = _run_cli(env=env)
        for tag in tp.TAG_VOCAB():
            self.assertIn(tag, out, f"registry-doc stdout missing tag {tag!r}")

    def test_renders_every_verify_mode(self):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        rc, out, _ = _run_cli(env=env)
        for mode in vmp.MODE_VOCAB():
            self.assertIn(mode, out, f"registry-doc stdout missing mode {mode!r}")

    def test_renders_section_headings(self):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        rc, out, _ = _run_cli(env=env)
        self.assertIn("## Task Types", out)
        self.assertIn("## Verify Modes", out)
        self.assertIn("resolved", out.lower())  # the "(resolved: baseline ⊕ overlay)" banner


class RegistryDocReadOnly(TestCase):
    """The safety contract: registry-doc never writes anywhere."""

    def test_takes_no_track_dir_and_writes_nothing(self):
        # Run in a temp dir with NO conductor/tracks/ (so no project overlay is
        # even discoverable). Snapshot the tree before/after — nothing changes.
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "CLAUDE_PROJECT_DIR": d}
            before = sorted(Path(d).rglob("*"))
            rc, out, err = _run_cli(env=env)
            self.assertEqual(rc, 0, f"failed in temp dir: {err}")
            after = sorted(Path(d).rglob("*"))
            self.assertEqual(before, after, "registry-doc wrote or deleted a file")

    def test_does_not_require_track_state_json(self):
        # registry-doc renders the REGISTRY, not any track's state — it must not
        # read or require a track-state.json. Confirmed implicitly above (temp
        # dir has none), but assert the explicit framing: no track-state.json
        # mention in stderr as a hard error.
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "CLAUDE_PROJECT_DIR": d}
            rc, out, err = _run_cli(env=env)
            self.assertEqual(rc, 0)
            self.assertNotIn("track-state.json", err.lower())


class RegistryDocOverlay(TestCase):
    """A project overlay's tag + mode appear in the resolved render."""

    def setUp(self):
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj

    def test_overlay_tag_and_mode_appear(self):
        with tempfile.TemporaryDirectory() as proj:
            Path(proj, "conductor", "tracks").mkdir(parents=True)
            Path(proj, "conductor", "workflow").mkdir(parents=True)
            Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(json.dumps({
                "tags": {"K8sRollout": {
                    "route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Deploy to a Kubernetes cluster via kubectl/helm.",
                    "signals": ["k8s", "kubectl", "helm", "rollout"],
                }},
            }))
            Path(proj, "conductor", "workflow", "verify-mode-profiles.json").write_text(json.dumps({
                "modes": {"smoke": {
                    "runs": ["boot-smoke"], "fix_policy": "none",
                    "when_to_use": "Post-deploy smoke against a live endpoint.",
                    "protocol": "Hit /healthz; gate on 200 OK.",
                }},
            }))
            env = {**os.environ, "CLAUDE_PROJECT_DIR": proj}
            rc, out, err = _run_cli(env=env)
            self.assertEqual(rc, 0, f"overlay render failed: {err}")
            self.assertIn("K8sRollout", out)  # overlay tag rendered
            self.assertIn("smoke", out)       # overlay mode rendered
            # Built-ins survive the overlay merge (render shows both).
            self.assertIn("Migrate", out)
            self.assertIn("compile", out)


class RegistryDocSanctioned(TestCase):
    """``registry-doc`` MUST be in the sanctioned subcommand set — otherwise the
    pre-command-check broad rm/mv/delete/move verb scan would false-positive on
    it (``registry`` contains none of those verbs today, but the sanctioned set
    is the explicit safety contract: a track-state CLI subcommand bypasses the
    scan because the CLI never deletes/moves track files)."""

    def test_registry_doc_is_sanctioned(self):
        self.assertIn("registry-doc", _pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_registry_doc_is_in_command_groups(self):
        # The sanctioned set is asserted (elsewhere) to cover _COMMAND_GROUPS;
        # mirror that the CLI actually registers the command, so a future edit
        # can't add it to one set and forget the other.
        from scripts.track_state.cli import _COMMAND_GROUPS
        all_cmds = {c for _, cmds in _COMMAND_GROUPS for c in cmds}
        self.assertIn("registry-doc", all_cmds)


if __name__ == "__main__":
    main()
