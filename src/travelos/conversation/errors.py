"""Typed sales-drafting errors (task 011a)."""

from __future__ import annotations


class SalesError(Exception):
    """Base class for sales drafting failures."""


class SalesValidationError(SalesError):
    """The request cannot produce a safe draft."""


class SalesNotFoundError(SalesError):
    """The inquiry, conversation or tenant does not exist."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"not found: {label}")


class SalesClosedError(SalesError):
    """A closed inquiry cannot receive a new customer reply."""

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"inquiry is closed in state {state}")
