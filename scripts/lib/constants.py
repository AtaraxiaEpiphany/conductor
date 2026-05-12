"""Shared constants for conductor hook scripts."""

# Failure patterns shared by on-subagent-stop.py and filter-subagent-output.py.
# Keep in sync — any change here affects both failure detection and recovery.
FAILURE_PATTERNS = [
    r"status.*FAILURE",
    r"BUILD FAILED",
    r"Traceback \(most recent call last\):",
    r"Command failed",
    r"test.*failed",
    r"Permission denied",
    r"File not found",
    r"AssertionError",
]

# Recovery success indicators (only meaningful after [Conductor Recovery] marker)
RECOVERY_SUCCESS_PATTERNS = [
    r"status.*SUCCESS",
    r"All tests passed",
    r"Coverage:",
]
