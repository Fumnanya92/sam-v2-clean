"""Sam v2 error categories."""

from enum import Enum


class ErrorType(str, Enum):
    MISSING_CAPABILITY = "missing_capability"
    MISSING_PERMISSION = "missing_permission"
    TOOL_FAILED = "tool_failed"
    COMMAND_FAILED = "command_failed"
    TEST_FAILED = "test_failed"
    FILE_ACCESS_ERROR = "file_access_error"
    GIT_ERROR = "git_error"
    MODEL_ERROR = "model_error"
    BROWSER_ERROR = "browser_error"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"
