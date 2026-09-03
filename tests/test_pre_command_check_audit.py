r"""Gap #11 — override audit. Each PreToolUse ``deny`` site appends a
``gate=<dangerous_git|state_lock|v10_commit|f2_tdd>`` line (plus a 12-char
command digest) to ``.data/logs/override-audit.log``. The hook denies outright
(no allow/deny prompt to wait on), so we log that a gate *fired* — the
actionable signal of which gates fire how often (and thus which denials a
long-running session routinely adapts around). The command is stored as a
digest, never verbatim.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _scripts / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pcc = _load("pre_command_check_g11", "pre-command-check.py")


def _run_main(command, cwd):
    """Drive main() with a Bash command. Returns the audit-log text (empty if none)."""
    td = tempfile.mkdtemp()
    prior = os.environ.get("CLAUDE_PLUGIN_DATA")
    os.environ["CLAUDE_PLUGIN_DATA"] = td
    # read_hook_input caches stdin module-globally; reset so this call re-reads.
    _pcc.read_hook_input.__globals__["_cached_hook_input"] = None
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }))
    sys.stdout = io.StringIO()
    try:
        _pcc.main()
    except SystemExit:
        pass  # write_hook_output exits 0/2 after deciding
    finally:
        sys.stdin, sys.stdout = old_in, old_out
        if prior is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = prior
    log = Path(td) / "logs" / "override-audit.log"
    return log.read_text() if log.exists() else ""


class AuditGateUnitTests(TestCase):
    def test_writes_gate_and_hex_digest(self):
        td = tempfile.mkdtemp()
        prior = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = td
        try:
            _pcc._audit_gate("dangerous_git", "git reset --hard HEAD~1")
        finally:
            if prior is None:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = prior
        log = (Path(td) / "logs" / "override-audit.log").read_text()
        self.assertIn("gate=dangerous_git", log)
        self.assertIn("digest=", log)
        digest = log.split("digest=")[1].strip()
        self.assertRegex(digest, r"^[0-9a-f]{12}$")

    def test_command_not_persisted_verbatim(self):
        td = tempfile.mkdtemp()
        prior = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = td
        try:
            _pcc._audit_gate("f2_tdd", "git commit -m 'feat: super secret thing'")
        finally:
            if prior is None:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = prior
        log = (Path(td) / "logs" / "override-audit.log").read_text()
        self.assertNotIn("super secret thing", log)

    def test_write_failure_does_not_raise(self):
        """_audit_gate is best-effort — it must never block the gate decision."""
        # Point CLAUDE_PLUGIN_DATA at a path that can't be created.
        prior = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = "/proc/cannot/create/here"
        try:
            _pcc._audit_gate("v10_commit", "git commit -m 'bad'")
        finally:
            if prior is None:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = prior


class MainAuditIntegrationTests(TestCase):
    def test_dangerous_git_command_audited(self):
        log = _run_main("git reset --hard HEAD~1", tempfile.mkdtemp())
        self.assertIn("gate=dangerous_git", log)

    def test_benign_command_not_audited(self):
        log = _run_main("ls -la", tempfile.mkdtemp())
        self.assertEqual(log, "")

    def test_feat_without_test_audited_as_f2_tdd(self):
        # A git repo with a staged source file (no test) → a feat commit trips F2.
        repo = tempfile.mkdtemp()
        for args in (["git", "init", repo],
                     ["git", "-C", repo, "config", "user.email", "t@t.com"],
                     ["git", "-C", repo, "config", "user.name", "T"]):
            subprocess.run(args, capture_output=True, check=True)
        src = Path(repo) / "src"
        src.mkdir()
        (src / "foo.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", repo, "add", "src/foo.py"],
                       capture_output=True, check=True)
        log = _run_main('git commit -m "feat(api): add foo"', repo)
        self.assertIn("gate=f2_tdd", log)

    def test_non_conventional_commit_audited_as_v10(self):
        log = _run_main('git commit -m "added a thing"', tempfile.mkdtemp())
        self.assertIn("gate=v10_commit", log)

    def test_sanctioned_append_handoff_not_state_locked(self):
        """Issue 1 end-to-end: the explorer's read-only append-handoff (whose
        heredoc findings mention remove/move/delete) must reach the allow path
        over an in_progress track — no state_lock ask, no audit entry."""
        root = tempfile.mkdtemp()
        td = Path(root) / "conductor" / "tracks" / "auth_20260706"
        td.mkdir(parents=True)
        (td / "track-state.json").write_text(json.dumps({
            "track_id": "auth_20260706", "status": "in_progress",
            "phases": [{"name": "P1", "status": "in_progress",
                        "tasks": [{"name": "T1", "status": "in_progress"}]}]}))
        (Path(root) / "conductor" / "tracks.md").write_text(
            "- [~] Auth (OAuth2) login (conductor/tracks/auth_20260706/)\n")
        command = (
            'track-state append-handoff "conductor/tracks/auth_20260706" '
            'P1 T1 --type explore << \'EOF\'\n'
            '{"findings":["remove the handler","move helper to utils","delete the cache"]}\n'
            'EOF'
        )
        log = _run_main(command, root)
        self.assertNotIn("state_lock", log)
        self.assertNotIn("dangerous_git", log)


if __name__ == "__main__":
    main()
