"""Generic deterministic state machines.

Definitions are immutable data. Firing a trigger is pure: it validates the
(state, trigger) pair, runs guards and returns the target state plus the side
effects the caller must emit through the outbox. Nothing here touches a
database, a clock or the network, which makes every transition table testable
and property-testable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from travelos.workflow.errors import (
    GuardRejectedError,
    IllegalTransitionError,
    UnknownStateError,
)

Context = Mapping[str, object]
GuardCheck = Callable[[Context], bool]
WILDCARD = "*"


def _allow(_context: Context) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class Guard:
    """A named, pure predicate over caller-supplied context."""

    name: str
    check: GuardCheck = field(default_factory=lambda: _allow)


@dataclass(frozen=True, slots=True)
class Transition:
    """One legal edge. ``source=WILDCARD`` means any non-terminal state."""

    trigger: str
    source: str
    target: str
    guards: tuple[Guard, ...] = ()
    effects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    initial: str
    states: frozenset[str]
    terminal: frozenset[str]
    transitions: tuple[Transition, ...]
    _by_key: dict[tuple[str, str], Transition] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.states:
            raise ValueError(f"{self.name}: at least one state is required")
        if self.initial not in self.states:
            raise ValueError(f"{self.name}: initial state {self.initial!r} is not declared")
        if not self.terminal <= self.states:
            unknown = self.terminal - self.states
            raise ValueError(f"{self.name}: terminal states not declared: {sorted(unknown)}")
        if self.initial in self.terminal:
            raise ValueError(f"{self.name}: initial state cannot be terminal")

        lookup: dict[tuple[str, str], Transition] = {}
        for transition in self.transitions:
            if transition.source != WILDCARD and transition.source not in self.states:
                raise ValueError(
                    f"{self.name}: transition source {transition.source!r} is not declared"
                )
            if transition.target not in self.states:
                raise ValueError(
                    f"{self.name}: transition target {transition.target!r} is not declared"
                )
            if transition.source in self.terminal:
                raise ValueError(
                    f"{self.name}: terminal state {transition.source!r} has an outgoing transition"
                )
            key = (transition.source, transition.trigger)
            if key in lookup:
                raise ValueError(
                    f"{self.name}: duplicate transition for {transition.source!r} --{transition.trigger}-->"
                )
            lookup[key] = transition
        object.__setattr__(self, "_by_key", lookup)

    def transition_from(self, state: str, trigger: str) -> Transition | None:
        """Exact transitions win; wildcard exits apply to non-terminal states."""

        exact = self._by_key.get((state, trigger))
        if exact is not None:
            return exact
        if state in self.terminal:
            return None
        return self._by_key.get((WILDCARD, trigger))


@dataclass(frozen=True, slots=True)
class TransitionResult:
    machine: str
    source: str
    trigger: str
    target: str
    effects: tuple[str, ...]


class StateMachine:
    """Runs one :class:`WorkflowDefinition`."""

    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def states(self) -> frozenset[str]:
        return self.definition.states

    def can_fire(self, state: str, trigger: str, context: Context | None = None) -> bool:
        if state not in self.definition.states:
            return False
        transition = self.definition.transition_from(state, trigger)
        if transition is None:
            return False
        return all(guard.check(context or {}) for guard in transition.guards)

    def fire(self, state: str, trigger: str, context: Context | None = None) -> TransitionResult:
        """Move one step or raise a typed workflow error."""

        if state not in self.definition.states:
            raise UnknownStateError(self.name, state)
        transition = self.definition.transition_from(state, trigger)
        if transition is None:
            raise IllegalTransitionError(self.name, state, trigger)

        resolved = context or {}
        for guard in transition.guards:
            if not guard.check(resolved):
                raise GuardRejectedError(self.name, state, trigger, guard.name)

        return TransitionResult(
            machine=self.name,
            source=state,
            trigger=trigger,
            target=transition.target,
            effects=transition.effects,
        )

    def available_triggers(self, state: str, context: Context | None = None) -> tuple[str, ...]:
        """Triggers that would succeed from ``state`` right now."""

        if state not in self.definition.states:
            raise UnknownStateError(self.name, state)
        resolved = context or {}
        triggers = {
            transition.trigger
            for transition in self.definition.transitions
            if transition.source == state
            or (transition.source == WILDCARD and state not in self.definition.terminal)
        }
        return tuple(
            sorted(trigger for trigger in triggers if self.can_fire(state, trigger, resolved))
        )
