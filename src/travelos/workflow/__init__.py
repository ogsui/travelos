"""Workflow engine: pure state machines, persistent timers and the outbox.

Domain code never sends messages or calls suppliers directly. A transition
returns the effects it requires; those effects are written to ``outbox_events``
in the same transaction as the state change and delivered at least once by a
worker. Consumers are idempotent, so redelivery cannot double-apply a side
effect.
"""

from travelos.workflow.definitions import DEFINITIONS, MACHINES, get_machine
from travelos.workflow.errors import (
    GuardRejectedError,
    IllegalTransitionError,
    UnknownStateError,
    UnknownWorkflowError,
    WorkflowError,
)
from travelos.workflow.state_machine import (
    Guard,
    StateMachine,
    Transition,
    TransitionResult,
    WorkflowDefinition,
)

__all__ = [
    "DEFINITIONS",
    "MACHINES",
    "Guard",
    "GuardRejectedError",
    "IllegalTransitionError",
    "StateMachine",
    "Transition",
    "TransitionResult",
    "UnknownStateError",
    "UnknownWorkflowError",
    "WorkflowDefinition",
    "WorkflowError",
    "get_machine",
]
