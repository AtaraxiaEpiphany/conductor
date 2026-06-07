"""Shared constants for conductor hook scripts."""

# Failure patterns shared by on-subagent-stop.py and filter-subagent-output.py.
# Keep in sync — any change here affects both failure detection and recovery.
# Patterns are anchored for precision to reduce false positives.
FAILURE_PATTERNS = [
    r"\bstatus:\s*FAILURE\b",
    r"\bBUILD FAILED\b",
    r"Traceback \(most recent call last\):",
    r"\bCommand failed\b",
    r"\b\d+\s+tests?\s+failed\b",
    r"\bPermission denied\b",
    r"\bFile not found\b",
    r"\bAssertionError\b",
]

# Recovery success indicators (only meaningful after [Conductor Recovery] marker)
RECOVERY_SUCCESS_PATTERNS = [
    r"status.*SUCCESS",
    r"All tests passed",
    r"Coverage:",
]

# Conventional commit format (V10 enforcement)
# Matches: type(scope): description — e.g. "feat(api): add user endpoint"
VALID_COMMIT_TYPES = r"(feat|fix|docs|style|refactor|test|chore)"
COMMIT_MSG_PATTERN = rf"^{VALID_COMMIT_TYPES}\([^)]+\):\s*.+"
