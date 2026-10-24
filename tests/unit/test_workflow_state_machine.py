"""State-machine tables, guards and illegal-transition property tests.

The expected edge table below is written out by hand from
``docs/02_ARCHITECTURE.md``. It is deliberately not derived from the
implementation, so a definition that drifts from the documented flow fails.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from travelos.workflow.definitions import DEFINITIONS, get_machine
from travelos.workflow.errors import (
    GuardRejectedError,
    IllegalTransitionError,
    UnknownStateError,
    UnknownWorkflowError,
)
from travelos.workflow.state_machine import (
    Guard,
    StateMachine,
    Transition,
    WorkflowDefinition,
)


@dataclass(frozen=True, slots=True)
class Edge:
    target: str
    effects: tuple[str, ...] = ()
    guards: tuple[str, ...] = ()


def _edge(target: str, *, effects: tuple[str, ...] = (), guards: tuple[str, ...] = ()) -> Edge:
    return Edge(target=target, effects=effects, guards=guards)


EXPECTED: dict[str, dict[tuple[str, str], Edge]] = {
    "inquiry": {
        ("RECEIVED", "parse"): _edge("PARSED", effects=("inquiry.parsed",)),
        ("PARSED", "missing_info"): _edge("NEEDS_INFO", effects=("inquiry.info_requested",)),
        ("NEEDS_INFO", "parse"): _edge("PARSED", effects=("inquiry.parsed",)),
        ("PARSED", "search"): _edge("SEARCHING", effects=("inquiry.search_requested",)),
        ("SEARCHING", "price"): _edge("PRICED", effects=("inquiry.priced",)),
        ("PRICED", "request_approval"): _edge("PENDING_APPROVAL"),
        ("PENDING_APPROVAL", "approve"): _edge("APPROVED", guards=("approved",)),
        ("APPROVED", "send_quote"): _edge("QUOTE_SENT", effects=("inquiry.quote_sent",)),
        ("QUOTE_SENT", "negotiate"): _edge("NEGOTIATING"),
        ("NEGOTIATING", "accept"): _edge("ACCEPTED"),
        ("ACCEPTED", "request_payment"): _edge("AWAITING_PAYMENT"),
        ("AWAITING_PAYMENT", "payment_received"): _edge("PAID"),
        ("PAID", "start_booking"): _edge("BOOKING"),
        ("BOOKING", "booking_confirmed"): _edge("BOOKED"),
        ("BOOKED", "invoice"): _edge("INVOICED"),
        ("INVOICED", "send_docs"): _edge("DOCS_SENT"),
        ("DOCS_SENT", "start_travel"): _edge("TRAVELLING"),
        ("TRAVELLING", "complete"): _edge("COMPLETED"),
        ("COMPLETED", "close"): _edge("CLOSED"),
        ("*", "reject"): _edge("REJECTED"),
        ("*", "expire"): _edge("EXPIRED"),
        ("*", "mark_lost"): _edge("LOST"),
        ("*", "cancel"): _edge("CANCELLED"),
    },
    "quote": {
        ("DRAFT", "request_approval"): _edge("PENDING_APPROVAL"),
        ("PENDING_APPROVAL", "approve"): _edge("APPROVED", guards=("approved",)),
        ("APPROVED", "send"): _edge("SENT", effects=("quote.sent",)),
        ("SENT", "mark_viewed"): _edge("VIEWED"),
        ("VIEWED", "accept"): _edge("ACCEPTED", effects=("quote.accepted",)),
        ("VIEWED", "expire"): _edge("EXPIRED"),
        ("VIEWED", "revise"): _edge("REVISED", effects=("quote.revision_requested",)),
        ("VIEWED", "reject"): _edge("REJECTED"),
    },
    "booking": {
        ("PENDING_CONFIRMATION", "confirm"): _edge("REVALIDATING", guards=("human_confirmed",)),
        ("REVALIDATING", "revalidated"): _edge("HELD"),
        ("HELD", "ticket"): _edge("TICKETING"),
        ("TICKETING", "confirm"): _edge("CONFIRMED", effects=("booking.confirmed",)),
        ("TICKETING", "partial_failure"): _edge("FAILED_PARTIAL", effects=("booking.partial",)),
        ("FAILED_PARTIAL", "needs_human"): _edge("NEEDS_HUMAN", effects=("booking.needs_human",)),
        ("CONFIRMED", "request_cancel"): _edge("CANCEL_REQUESTED"),
        ("CANCEL_REQUESTED", "cancel"): _edge("CANCELLED", effects=("booking.cancelled",)),
    },
    "payment": {
        ("LINK_CREATED", "partial_payment"): _edge("PARTIAL"),
        ("LINK_CREATED", "paid"): _edge("PAID", effects=("payment.received",)),
        ("LINK_CREATED", "fail"): _edge("FAILED"),
        ("LINK_CREATED", "expire"): _edge("EXPIRED"),
        ("PARTIAL", "paid"): _edge("PAID", effects=("payment.received",)),
        ("PARTIAL", "fail"): _edge("FAILED"),
        ("PARTIAL", "expire"): _edge("EXPIRED"),
        ("PAID", "request_refund"): _edge("REFUND_REQUESTED"),
        ("REFUND_REQUESTED", "refund"): _edge("REFUNDED", effects=("payment.refund_recorded",)),
    },
    "campaign": {
        ("DRAFT", "submit_audience"): _edge("AUDIENCE_PENDING"),
        ("AUDIENCE_PENDING", "approve_audience"): _edge(
            "CONTENT_PENDING", guards=("audience_approved",)
        ),
        ("CONTENT_PENDING", "approve_content"): _edge("SCHEDULED", guards=("content_approved",)),
        ("SCHEDULED", "start"): _edge("RUNNING"),
        ("RUNNING", "pause"): _edge("PAUSED"),
        ("PAUSED", "resume"): _edge("RUNNING"),
        ("RUNNING", "complete"): _edge("COMPLETED"),
    },
    "content_item": {
        ("IDEA", "draft"): _edge("DRAFT"),
        ("DRAFT", "request_approval"): _edge("PENDING_APPROVAL"),
        ("PENDING_APPROVAL", "approve"): _edge("SCHEDULED", guards=("approved",)),
        ("PENDING_APPROVAL", "reject"): _edge("REJECTED"),
        ("SCHEDULED", "publish"): _edge("PUBLISHED", effects=("content.published",)),
        ("PUBLISHED", "analyze"): _edge("ANALYZED"),
    },
}

GUARD_CONTEXT: dict[str, object] = {
    "approved": True,
    "human_confirmed": True,
    "audience_approved": True,
    "content_approved": True,
}

ALL_TRIGGERS = sorted({trigger for edges in EXPECTED.values() for (_, trigger) in edges})


def legal_edge(state: str, trigger: str, machine: StateMachine) -> Edge | None:
    edges = EXPECTED[machine.name]
    exact = edges.get((state, trigger))
    if exact is not None:
        return exact
    if state in machine.definition.terminal:
        return None
    return edges.get(("*", trigger))


def iter_state_cases() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for name, machine in DEFINITIONS.items():
        for state in sorted(machine.states):
            cases.append((name, state))
    return cases


@pytest.mark.parametrize(("name", "state"), iter_state_cases())
def test_every_state_and_trigger_matches_the_documented_table(name: str, state: str) -> None:
    machine = get_machine(name)
    for trigger in ALL_TRIGGERS:
        edge = legal_edge(state, trigger, machine)
        label = f"{name}: {state!r} --{trigger}-->"

        if edge is None:
            with pytest.raises(IllegalTransitionError):
                machine.fire(state, trigger, GUARD_CONTEXT)
            continue

        result = machine.fire(state, trigger, GUARD_CONTEXT)
        assert result.source == state, label
        assert result.target == edge.target, label
        assert result.effects == edge.effects, label
        assert result.machine == name, label

        if edge.guards:
            with pytest.raises(GuardRejectedError) as excinfo:
                machine.fire(state, trigger, {})
            assert excinfo.value.guard == edge.guards[0], label


@pytest.mark.parametrize("name", sorted(DEFINITIONS))
def test_initial_state_and_terminal_states_are_consistent(name: str) -> None:
    definition = DEFINITIONS[name]
    assert definition.initial in definition.states
    assert definition.terminal <= definition.states

    for transition in definition.transitions:
        assert transition.source not in definition.terminal, (
            f"{name}: terminal {transition.source} has an outgoing edge"
        )


@pytest.mark.parametrize("name", sorted(DEFINITIONS))
def test_terminal_states_reject_every_trigger(name: str) -> None:
    machine = get_machine(name)
    for state in sorted(machine.definition.terminal):
        assert machine.available_triggers(state, GUARD_CONTEXT) == ()
        for trigger in ALL_TRIGGERS:
            with pytest.raises(IllegalTransitionError):
                machine.fire(state, trigger, GUARD_CONTEXT)


@pytest.mark.parametrize("name", sorted(DEFINITIONS))
def test_unknown_state_is_rejected(name: str) -> None:
    machine = get_machine(name)
    with pytest.raises(UnknownStateError):
        machine.fire("NOT_A_STATE", "anything")
    assert machine.can_fire("NOT_A_STATE", "anything") is False
    with pytest.raises(UnknownStateError):
        machine.available_triggers("NOT_A_STATE")


@given(
    machine_name=st.sampled_from(sorted(DEFINITIONS)),
    state=st.sampled_from(sorted({s for m in DEFINITIONS.values() for s in m.states})),
    suffix=st.text(min_size=0, max_size=30),
)
@settings(max_examples=200, deadline=None)
def test_arbitrary_trigger_never_produces_an_undeclared_target(
    machine_name: str, state: str, suffix: str
) -> None:
    machine = get_machine(machine_name)
    if state not in machine.states:
        return
    trigger = f"not-a-trigger-{suffix}"
    try:
        result = machine.fire(state, trigger, GUARD_CONTEXT)
    except IllegalTransitionError:
        return
    assert result.target in machine.states
    assert result.source in machine.states
    assert result.trigger == trigger


def test_available_triggers_respects_guards_and_wildcards() -> None:
    booking = get_machine("booking")
    assert booking.available_triggers("PENDING_CONFIRMATION") == ()
    assert booking.available_triggers("PENDING_CONFIRMATION", {"human_confirmed": True}) == (
        "confirm",
    )

    inquiry = get_machine("inquiry")
    assert inquiry.available_triggers("RECEIVED", GUARD_CONTEXT) == (
        "cancel",
        "expire",
        "mark_lost",
        "parse",
        "reject",
    )


def test_guard_defaults_to_allow() -> None:
    guard = Guard("always")
    assert guard.check({}) is True


def test_unknown_workflow_name_raises_a_typed_error() -> None:
    with pytest.raises(UnknownWorkflowError):
        get_machine("telepathy")


# --------------------------------------------------------------------------
# Definition validation
# --------------------------------------------------------------------------


def _definition(
    *,
    states: set[str] | None = None,
    terminal: set[str] | None = None,
    initial: str = "A",
    transitions: list[Transition] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="test",
        initial=initial,
        states=frozenset(states or {"A", "B"}),
        terminal=frozenset(terminal or {"B"}),
        transitions=tuple(transitions or [Transition("go", "A", "B")]),
    )


def test_definition_requires_at_least_one_state() -> None:
    with pytest.raises(ValueError, match="at least one state"):
        WorkflowDefinition(
            name="empty",
            initial="A",
            states=frozenset(),
            terminal=frozenset(),
            transitions=(),
        )


def test_definition_rejects_unknown_initial_state() -> None:
    with pytest.raises(ValueError, match="initial state"):
        _definition(initial="Z")


def test_definition_rejects_undeclared_terminal_state() -> None:
    with pytest.raises(ValueError, match="terminal states not declared"):
        _definition(terminal={"B", "Z"})


def test_definition_rejects_terminal_initial_state() -> None:
    with pytest.raises(ValueError, match="initial state cannot be terminal"):
        _definition(initial="B", terminal={"B"}, transitions=[])


def test_definition_rejects_unknown_transition_source_and_target() -> None:
    with pytest.raises(ValueError, match="source 'Z' is not declared"):
        _definition(transitions=[Transition("go", "Z", "B")])
    with pytest.raises(ValueError, match="target 'Z' is not declared"):
        _definition(transitions=[Transition("go", "A", "Z")])


def test_definition_rejects_outgoing_edges_from_terminal_states() -> None:
    with pytest.raises(ValueError, match="has an outgoing transition"):
        _definition(transitions=[Transition("go", "A", "B"), Transition("back", "B", "A")])


def test_definition_rejects_duplicate_source_and_trigger() -> None:
    with pytest.raises(ValueError, match="duplicate transition"):
        _definition(transitions=[Transition("go", "A", "B"), Transition("go", "A", "B")])


def test_wildcard_does_not_apply_to_terminal_states() -> None:
    definition = WorkflowDefinition(
        name="wild",
        initial="A",
        states=frozenset({"A", "B", "C"}),
        terminal=frozenset({"B"}),
        transitions=(
            Transition("go", "A", "B"),
            Transition("cancel", "*", "C"),
        ),
    )
    machine = StateMachine(definition)
    assert machine.fire("A", "cancel").target == "C"
    assert machine.definition.transition_from("B", "cancel") is None


def test_wildcard_effect_table_is_exhaustive() -> None:
    """Every non-terminal state can take every documented exit trigger."""

    inquiry = get_machine("inquiry")
    exit_triggers = {trigger for (source, trigger) in EXPECTED["inquiry"] if source == "*"}
    for state in inquiry.states - inquiry.definition.terminal:
        for trigger in exit_triggers:
            result = inquiry.fire(state, trigger, GUARD_CONTEXT)
            assert result.target == EXPECTED["inquiry"][("*", trigger)].target
