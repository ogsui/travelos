"""Typed approval errors.

API routes translate these to HTTP status codes; service callers can catch
them without depending on FastAPI.
"""

from __future__ import annotations


class ApprovalError(Exception):
    """Base class for approval and autonomy failures."""


class UnknownActionError(ApprovalError):
    """The action type is not in the catalogue; fail closed."""

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type
        super().__init__(f"unknown action type: {action_type!r}")


class PolicyViolationError(ApprovalError):
    """The requested autonomy level is locked or above the catalogue ceiling."""

    def __init__(self, action_type: str, requested: str, allowed: str, reason: str) -> None:
        self.action_type = action_type
        self.requested = requested
        self.allowed = allowed
        self.reason = reason
        super().__init__(f"{action_type}: cannot set {requested} ({reason}; allowed {allowed})")


class PolicyDeniedError(ApprovalError):
    """The action is A0: a human must do it, the system may only prepare data."""

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type
        super().__init__(f"{action_type} is A0: a human must perform this action")


class ApprovalNotFoundError(ApprovalError):
    """No approval request with that id exists for the tenant."""


class ApprovalStateError(ApprovalError):
    """The request is no longer pending, so it cannot be decided or expired."""

    def __init__(self, request_id: str, status: str) -> None:
        self.request_id = request_id
        self.status = status
        super().__init__(f"approval {request_id} is {status}, not pending")


class SelfApprovalError(ApprovalError):
    """The requester may never decide their own request."""


class DistinctReviewerError(ApprovalError):
    """A four-eyes request needs two different reviewers."""


class ApprovalPermissionError(ApprovalError):
    """The reviewer's role is not allowed to decide this request."""

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type
        super().__init__(f"{action_type} may only be decided by the owner")


class ReasonRequiredError(ApprovalError):
    """Rejections must carry a reason (docs/05_APPROVAL_MATRIX.md)."""


class EditRequiresChangeError(ApprovalError):
    """An edit decision must supply a final payload that differs from the proposal."""


class UnhandledTimerError(ApprovalError):
    """A ``timer.fired`` event has no registered handler; let it dead-letter."""
