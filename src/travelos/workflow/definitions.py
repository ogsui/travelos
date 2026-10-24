"""Authoritative workflow definitions from docs/02_ARCHITECTURE.md.

States and edges mirror the documented state machines. Guards encode the human
gates that must never become "auto-approved": quote/booking confirmation,
campaign audience and content approval, and item approval. Effects are event
type names emitted through the transactional outbox.
"""

from __future__ import annotations

from travelos.workflow.errors import UnknownWorkflowError
from travelos.workflow.state_machine import (
    Guard,
    StateMachine,
    Transition,
    WorkflowDefinition,
)

# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def _is_true(key: str) -> Guard:
    return Guard(key, lambda context: context.get(key) is True)


APPROVED = _is_true("approved")
HUMAN_CONFIRMED = _is_true("human_confirmed")
AUDIENCE_APPROVED = _is_true("audience_approved")
CONTENT_APPROVED = _is_true("content_approved")


def _t(
    trigger: str,
    source: str,
    target: str,
    *guards: Guard,
    effects: tuple[str, ...] = (),
) -> Transition:
    return Transition(
        trigger=trigger,
        source=source,
        target=target,
        guards=guards,
        effects=effects,
    )


def _definition(
    name: str,
    initial: str,
    states: set[str],
    terminal: set[str],
    transitions: list[Transition],
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        initial=initial,
        states=frozenset(states),
        terminal=frozenset(terminal),
        transitions=tuple(transitions),
    )


# --------------------------------------------------------------------------
# Inquiry
# --------------------------------------------------------------------------

INQUIRY = _definition(
    "inquiry",
    "RECEIVED",
    {
        "RECEIVED",
        "PARSED",
        "NEEDS_INFO",
        "SEARCHING",
        "PRICED",
        "PENDING_APPROVAL",
        "APPROVED",
        "QUOTE_SENT",
        "NEGOTIATING",
        "ACCEPTED",
        "AWAITING_PAYMENT",
        "PAID",
        "BOOKING",
        "BOOKED",
        "INVOICED",
        "DOCS_SENT",
        "TRAVELLING",
        "COMPLETED",
        "CLOSED",
        "REJECTED",
        "EXPIRED",
        "LOST",
        "CANCELLED",
    },
    {"CLOSED", "REJECTED", "EXPIRED", "LOST", "CANCELLED"},
    [
        _t("parse", "RECEIVED", "PARSED", effects=("inquiry.parsed",)),
        _t("missing_info", "PARSED", "NEEDS_INFO", effects=("inquiry.info_requested",)),
        _t("parse", "NEEDS_INFO", "PARSED", effects=("inquiry.parsed",)),
        _t("search", "PARSED", "SEARCHING", effects=("inquiry.search_requested",)),
        _t("price", "SEARCHING", "PRICED", effects=("inquiry.priced",)),
        _t("request_approval", "PRICED", "PENDING_APPROVAL"),
        _t("approve", "PENDING_APPROVAL", "APPROVED", APPROVED),
        _t("send_quote", "APPROVED", "QUOTE_SENT", effects=("inquiry.quote_sent",)),
        _t("negotiate", "QUOTE_SENT", "NEGOTIATING"),
        _t("accept", "NEGOTIATING", "ACCEPTED"),
        _t("request_payment", "ACCEPTED", "AWAITING_PAYMENT"),
        _t("payment_received", "AWAITING_PAYMENT", "PAID"),
        _t("start_booking", "PAID", "BOOKING"),
        _t("booking_confirmed", "BOOKING", "BOOKED"),
        _t("invoice", "BOOKED", "INVOICED"),
        _t("send_docs", "INVOICED", "DOCS_SENT"),
        _t("start_travel", "DOCS_SENT", "TRAVELLING"),
        _t("complete", "TRAVELLING", "COMPLETED"),
        _t("close", "COMPLETED", "CLOSED"),
        _t("reject", "*", "REJECTED"),
        _t("expire", "*", "EXPIRED"),
        _t("mark_lost", "*", "LOST"),
        _t("cancel", "*", "CANCELLED"),
    ],
)


# --------------------------------------------------------------------------
# Quote
# --------------------------------------------------------------------------

QUOTE = _definition(
    "quote",
    "DRAFT",
    {
        "DRAFT",
        "PENDING_APPROVAL",
        "APPROVED",
        "SENT",
        "VIEWED",
        "ACCEPTED",
        "EXPIRED",
        "REVISED",
        "REJECTED",
    },
    {"ACCEPTED", "EXPIRED", "REVISED", "REJECTED"},
    [
        _t("request_approval", "DRAFT", "PENDING_APPROVAL"),
        _t("approve", "PENDING_APPROVAL", "APPROVED", APPROVED),
        _t("send", "APPROVED", "SENT", effects=("quote.sent",)),
        _t("mark_viewed", "SENT", "VIEWED"),
        _t("accept", "VIEWED", "ACCEPTED", effects=("quote.accepted",)),
        _t("expire", "VIEWED", "EXPIRED"),
        _t("revise", "VIEWED", "REVISED", effects=("quote.revision_requested",)),
        _t("reject", "VIEWED", "REJECTED"),
    ],
)


# --------------------------------------------------------------------------
# Booking
# --------------------------------------------------------------------------

BOOKING = _definition(
    "booking",
    "PENDING_CONFIRMATION",
    {
        "PENDING_CONFIRMATION",
        "REVALIDATING",
        "HELD",
        "TICKETING",
        "CONFIRMED",
        "FAILED_PARTIAL",
        "NEEDS_HUMAN",
        "CANCEL_REQUESTED",
        "CANCELLED",
    },
    {"CANCELLED"},
    [
        _t("confirm", "PENDING_CONFIRMATION", "REVALIDATING", HUMAN_CONFIRMED),
        _t("revalidated", "REVALIDATING", "HELD"),
        _t("ticket", "HELD", "TICKETING"),
        _t("confirm", "TICKETING", "CONFIRMED", effects=("booking.confirmed",)),
        _t("partial_failure", "TICKETING", "FAILED_PARTIAL", effects=("booking.partial",)),
        _t("needs_human", "FAILED_PARTIAL", "NEEDS_HUMAN", effects=("booking.needs_human",)),
        _t("request_cancel", "CONFIRMED", "CANCEL_REQUESTED"),
        _t("cancel", "CANCEL_REQUESTED", "CANCELLED", effects=("booking.cancelled",)),
    ],
)


# --------------------------------------------------------------------------
# Payment
# --------------------------------------------------------------------------

PAYMENT = _definition(
    "payment",
    "LINK_CREATED",
    {
        "LINK_CREATED",
        "PARTIAL",
        "PAID",
        "FAILED",
        "EXPIRED",
        "REFUND_REQUESTED",
        "REFUNDED",
    },
    {"FAILED", "EXPIRED", "REFUNDED"},
    [
        _t("partial_payment", "LINK_CREATED", "PARTIAL"),
        _t("paid", "LINK_CREATED", "PAID", effects=("payment.received",)),
        _t("fail", "LINK_CREATED", "FAILED"),
        _t("expire", "LINK_CREATED", "EXPIRED"),
        _t("paid", "PARTIAL", "PAID", effects=("payment.received",)),
        _t("fail", "PARTIAL", "FAILED"),
        _t("expire", "PARTIAL", "EXPIRED"),
        _t("request_refund", "PAID", "REFUND_REQUESTED"),
        _t("refund", "REFUND_REQUESTED", "REFUNDED", effects=("payment.refund_recorded",)),
    ],
)


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------

CAMPAIGN = _definition(
    "campaign",
    "DRAFT",
    {
        "DRAFT",
        "AUDIENCE_PENDING",
        "CONTENT_PENDING",
        "SCHEDULED",
        "RUNNING",
        "PAUSED",
        "COMPLETED",
    },
    {"COMPLETED"},
    [
        _t("submit_audience", "DRAFT", "AUDIENCE_PENDING"),
        _t("approve_audience", "AUDIENCE_PENDING", "CONTENT_PENDING", AUDIENCE_APPROVED),
        _t("approve_content", "CONTENT_PENDING", "SCHEDULED", CONTENT_APPROVED),
        _t("start", "SCHEDULED", "RUNNING"),
        _t("pause", "RUNNING", "PAUSED"),
        _t("resume", "PAUSED", "RUNNING"),
        _t("complete", "RUNNING", "COMPLETED"),
    ],
)


# --------------------------------------------------------------------------
# ContentItem
# --------------------------------------------------------------------------

CONTENT_ITEM = _definition(
    "content_item",
    "IDEA",
    {"IDEA", "DRAFT", "PENDING_APPROVAL", "SCHEDULED", "PUBLISHED", "ANALYZED", "REJECTED"},
    {"ANALYZED", "REJECTED"},
    [
        _t("draft", "IDEA", "DRAFT"),
        _t("request_approval", "DRAFT", "PENDING_APPROVAL"),
        _t("approve", "PENDING_APPROVAL", "SCHEDULED", APPROVED),
        _t("reject", "PENDING_APPROVAL", "REJECTED"),
        _t("publish", "SCHEDULED", "PUBLISHED", effects=("content.published",)),
        _t("analyze", "PUBLISHED", "ANALYZED"),
    ],
)


DEFINITIONS: dict[str, WorkflowDefinition] = {
    definition.name: definition
    for definition in (INQUIRY, QUOTE, BOOKING, PAYMENT, CAMPAIGN, CONTENT_ITEM)
}

MACHINES: dict[str, StateMachine] = {
    name: StateMachine(definition) for name, definition in DEFINITIONS.items()
}


def get_machine(name: str) -> StateMachine:
    """Look up a machine by name, failing loudly for typos."""

    try:
        return MACHINES[name]
    except KeyError as exc:
        raise UnknownWorkflowError(name) from exc
