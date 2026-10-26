"""Template registry rules (task 011a)."""

from __future__ import annotations

import pytest

from travelos.conversation.templates import (
    ACKNOWLEDGEMENT_TEMPLATES,
    MessageTemplate,
    TemplateNotApprovedError,
    TemplateNotFoundError,
    TemplateRegistry,
    select_acknowledgement,
)


def template(
    key: str = "ack_en",
    *,
    language: str = "en",
    approved: bool = True,
    body: str = "Namaste! We have received your enquiry.",
) -> MessageTemplate:
    return MessageTemplate(
        key=key,
        kind="acknowledgement",
        language=language,
        body=body,
        owner_approved=approved,
    )


def test_exact_language_wins_and_english_is_the_fallback() -> None:
    registry = TemplateRegistry(
        [
            template("ack_hi", language="hi", body="Synthetic Hindi acknowledgement."),
            template("ack_en", language="en"),
        ]
    )

    assert registry.acknowledgement("hi").key == "ack_hi"
    assert registry.acknowledgement("hinglish").key == "ack_en"
    assert registry.acknowledgement("unknown").key == "ack_en"


def test_missing_language_has_no_template() -> None:
    registry = TemplateRegistry([template("ack_hi", language="hi")])
    with pytest.raises(TemplateNotFoundError):
        registry.acknowledgement("en")


def test_unapproved_template_is_refused() -> None:
    entry = template(approved=False)
    registry = TemplateRegistry([entry])
    with pytest.raises(TemplateNotApprovedError, match="ack_en"):
        registry.require_approved(entry)
    assert registry.require_approved(template()).key == "ack_en"


def test_duplicate_keys_and_bad_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate template key"):
        TemplateRegistry([template("same"), template("same", language="hi")])
    with pytest.raises(ValueError, match="unsupported template language"):
        TemplateRegistry([template(language="fr")])
    with pytest.raises(ValueError, match="empty body"):
        TemplateRegistry([template(body="   ")])
    with pytest.raises(ValueError, match="unsupported template kind"):
        TemplateRegistry(
            [
                MessageTemplate(
                    key="bad",
                    kind="sales_reply",  # type: ignore[arg-type]
                    language="en",
                    body="Hello",
                    owner_approved=True,
                )
            ]
        )
    with pytest.raises(ValueError, match="at least one template"):
        TemplateRegistry([])


def test_shipped_placeholder_is_not_owner_approved() -> None:
    """The repository must not claim owner approval it never received."""

    registry = TemplateRegistry(ACKNOWLEDGEMENT_TEMPLATES)
    entry = registry.acknowledgement("en")
    assert entry.owner_approved is False
    assert "TODO(owner)" in entry.notes
    with pytest.raises(TemplateNotApprovedError):
        registry.require_approved(entry)


def test_select_helper_uses_the_same_rules() -> None:
    assert select_acknowledgement([template("ack_en")], "hi").key == "ack_en"
