"""Shared constants for conductor hook scripts."""

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
