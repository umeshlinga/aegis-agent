"""Error hierarchy.

Every failure in Aegis maps to one of these classes. The class decides
whether a retry is allowed, and the failure taxonomy (tracing/taxonomy.py)
uses the class name to label failed runs. Keeping this explicit is what lets
recovery logic be enforced in code instead of left to the model.
"""
from __future__ import annotations


class AegisError(Exception):
    """Base class for all expected failures."""


# ---- Retryable: the same call may succeed if tried again -------------------
class TransientError(AegisError):
    pass


class ToolTimeout(TransientError):
    pass


class RateLimited(TransientError):
    def __init__(self, msg: str = "rate limited", retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class UpstreamServerError(TransientError):
    pass


class MalformedResponse(TransientError):
    """Upstream returned something we could not parse. Retrying often helps."""


class CircuitOpen(TransientError):
    def __init__(self, msg: str = "circuit open", retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class BrowserNoEffect(TransientError):
    """A browser action ran but the page did not change the way it should have."""


class LocatorLost(TransientError):
    """The element the agent wanted is gone (re-render, navigation, overlay)."""


# ---- Not retryable: retrying the same call will fail the same way ----------
class PermanentError(AegisError):
    pass


class NotFound(PermanentError):
    pass


class PermissionDenied(PermanentError):
    pass


class LoopDetected(PermanentError):
    pass


class BudgetExceeded(PermanentError):
    pass


class SchemaViolation(PermanentError):
    """Model output failed validation after all repair attempts."""


class VerificationFailed(PermanentError):
    pass


class InjectionBlocked(PermanentError):
    pass


class DomainNotAllowed(PermanentError):
    pass


# ---- Control flow -----------------------------------------------------------
class ApprovalRequired(AegisError):
    """Raised by the tool layer when a high-risk action needs a human decision."""

    def __init__(self, approval_id: int, tool: str, reason: str):
        super().__init__(f"approval {approval_id} required for {tool}: {reason}")
        self.approval_id = approval_id
        self.tool = tool
        self.reason = reason


class Cancelled(AegisError):
    pass


class SimulatedCrash(BaseException):
    """Emulates the process dying mid-run (kill -9, OOM, deploy).

    Inherits from BaseException on purpose so that ordinary `except Exception`
    handlers cannot swallow it. State is left exactly as a real crash would
    leave it, which is what the resume path must handle.
    """


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TransientError)
