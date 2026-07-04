"""Pure coverage-output parsing (no subprocess).

Shared by ``on-batch-complete.py`` (server-side F3 probe) and the
``test-digester`` agent (implementation-loop coverage digestion, via
``scripts/coverage-pct.py``). Keeping the parser pure and process-free lets
both call sites supply output text they already captured, so parsing is
deterministic and unit-testable without running real tools — and a haiku
digester never re-derives the per-language regex in prose (which drifts).

The subprocess-running wrapper (``get_coverage_percent``) stays in
``on-batch-complete.py``: only the hook needs to spawn the tool. The digester
runs the test command itself and pipes the captured stdout through
``scripts/coverage-pct.py``, which calls ``parse_coverage_percent`` here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def detect_project_type(cwd: Path) -> Optional[str]:
    """Detect project type from marker files present in ``cwd``.

    Returns ``"python"``, ``"node"``, ``"go"``, or ``None``.
    """
    if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
        return "python"
    if (cwd / "package.json").exists():
        return "node"
    if (cwd / "go.mod").exists():
        return "go"
    return None


def parse_coverage_percent(output: str, project_type: Optional[str]) -> Optional[float]:
    """Parse a coverage percentage from captured tool output, by project type.

    Returns the percentage as a float, or ``None`` if not found / unknown type.
    Pure: no subprocess, no filesystem. Language heuristics (preserved
    behavior-from ``on-batch-complete.py``'s former inline parser):

    - **python** (coverage.py) — the trailing percent on the ``TOTAL`` line.
    - **node** (jest/vitest) — the first number on a line containing
      ``All files`` or ``% Statements``.
    - **go** — ``coverage: 87.5% of statements``.
    """
    if not output or not project_type:
        return None

    if project_type == "python":
        # Coverage.py: "TOTAL                             100      100    100.00%"
        for line in output.split('\n'):
            if line.strip().startswith("TOTAL"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        return float(parts[-1].rstrip('%'))
                    except (ValueError, IndexError):
                        continue
    elif project_type == "node":
        # Jest: "All files | 85.5 | ..."
        for line in output.split('\n'):
            if "All files" in line or "% Statements" in line:
                match = re.search(r'(\d+\.?\d*)\s*%?', line)
                if match:
                    try:
                        return float(match.group(1))
                    except ValueError:
                        pass
    elif project_type == "go":
        # go test -cover: "coverage: 87.5% of statements"
        match = re.search(r'coverage:\s*(\d+\.?\d*)%', output)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

    return None
