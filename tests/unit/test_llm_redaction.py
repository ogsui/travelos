"""Reversible PII placeholders for LLM prompts."""

from __future__ import annotations

from travelos.llm.redaction import RedactionSession


def test_detected_pii_is_placeholder_bound_and_restored() -> None:
    session = RedactionSession()
    original = (
        "Email asha@example.test or call +91 90000 00001. "
        "Passport M1234567, card 4111 1111 1111 1111, Aadhaar 1234 5678 9012."
    )

    redacted = session.redact(original)

    for secret in ("asha@example.test", "90000", "M1234567", "4111", "1234 5678 9012"):
        assert secret not in redacted
    assert "[[EMAIL_1]]" in redacted
    assert "[[PHONE_1]]" in redacted
    assert "[[PASSPORT_1]]" in redacted
    assert "[[CARD_1]]" in redacted
    assert session.restore(redacted) == original


def test_same_value_gets_the_same_placeholder() -> None:
    session = RedactionSession()

    first = session.redact("write to asha@example.test")
    second = session.redact("again: asha@example.test")

    assert "[[EMAIL_1]]" in first
    assert "[[EMAIL_1]]" in second
    assert "[[EMAIL_2]]" not in second


def test_sensitive_value_is_redacted_even_without_a_pattern() -> None:
    session = RedactionSession()

    redacted = session.redact_value("Asha Synthetic", label="customer_name")

    assert redacted == "[[CUSTOMER_NAME_1]]"
    assert session.restore("Hello [[CUSTOMER_NAME_1]]") == "Hello Asha Synthetic"


def test_user_supplied_placeholder_cannot_be_restored() -> None:
    session = RedactionSession()
    session.redact("asha@example.test")

    redacted = session.redact("literal [[EMAIL_1]] from the customer")

    assert "[[EMAIL_1]]" not in redacted
    assert session.restore(redacted) == redacted


def test_unknown_placeholder_is_replaced_with_a_safe_marker() -> None:
    session = RedactionSession()

    assert session.restore("[[EMAIL_99]]") == "[REDACTED]"


def test_redacted_log_value_names_labels_only() -> None:
    session = RedactionSession()
    session.redact("asha@example.test and +919000000001")

    assert session.redacted_log_value() == "EMAIL=1, PHONE=1"
    assert "asha" not in session.redacted_log_value()


def test_clear_forgets_the_mapping() -> None:
    session = RedactionSession()
    session.redact("asha@example.test")

    session.clear()

    assert session.placeholders == ()
    assert session.restore("[[EMAIL_1]]") == "[REDACTED]"
