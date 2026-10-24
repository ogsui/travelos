"""Workflow domain errors."""

from __future__ import annotations

from travelos.core.errors import ValidationError


class WorkflowError(ValidationError):
    """Base class for state-machine violations."""


class UnknownWorkflowError(WorkflowError):
    """No workflow definition is registered under that name."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"unknown workflow {name!r}")


class UnknownStateError(WorkflowError):
    """A state that is not part of the machine definition."""

    def __init__(self, machine: str, state: str) -> None:
        self.machine = machine
        self.state = state
        super().__init__(f"{machine}: unknown state {state!r}")


class IllegalTransitionError(WorkflowError):
    """No transition exists for (state, trigger)."""

    def __init__(self, machine: str, source: str, trigger: str) -> None:
        self.machine = machine
        self.source = source
        self.trigger = trigger
        super().__init__(f"{machine}: illegal transition {source!r} --{trigger}-->")


class GuardRejectedError(WorkflowError):
    """A guard refused an otherwise legal transition."""

    def __init__(self, machine: str, source: str, trigger: str, guard: str) -> None:
        self.machine = machine
        self.source = source
        self.trigger = trigger
        self.guard = guard
        super().__init__(f"{machine}: guard {guard!r} rejected {source!r} --{trigger}-->")
