"""Pure quote-group option rules (task 016b1).

A group is the multi-option presentation of several quotes under one inquiry. The
labels and order are supplied by staff; nothing here ranks value/standard/premium
or derives a label from a price. The tests pin the structural rules only.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from travelos.quotes.models import QuoteGroupDraft, QuoteGroupOption

FIRST = UUID(int=1)
SECOND = UUID(int=2)


def option(label: str, quote_id: UUID = FIRST) -> QuoteGroupOption:
    return QuoteGroupOption(label=label, quote_id=quote_id)


def draft(*options: QuoteGroupOption) -> QuoteGroupDraft:
    return QuoteGroupDraft(options=options)


def test_two_distinct_options_are_valid() -> None:
    options = (option("Option A", FIRST), option("Option B", SECOND))

    assert draft(*options).options == options


def test_a_single_option_is_not_a_multi_option_group() -> None:
    with pytest.raises(ValidationError, match="at least two"):
        draft(option("Only", FIRST))


def test_duplicate_labels_are_refused_case_and_space_insensitively() -> None:
    with pytest.raises(ValidationError, match="labels must be unique"):
        draft(option("Beach", FIRST), option("  beach  ", SECOND))


def test_duplicate_quote_ids_are_refused() -> None:
    with pytest.raises(ValidationError, match="quotes must be unique"):
        draft(option("Option A", FIRST), option("Option B", FIRST))


def test_a_blank_label_is_refused() -> None:
    with pytest.raises(ValidationError):
        option("   ", FIRST)


def test_a_label_is_trimmed() -> None:
    assert option("  Option A  ", FIRST).label == "Option A"
