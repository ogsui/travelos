"""Redaction and JSON logging tests."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import SecretStr

from travelos.core.logging import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from travelos.core.redaction import REDACTED, RedactionFilter, redact_mapping, redact_text


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("email priya.sharma@example.com now", "priya.sharma@example.com"),
        ("call +91 98765 43210", "98765 43210"),
        ("passport M1234567", "M1234567"),
        ("card 4111 1111 1111 1111", "4111 1111 1111 1111"),
        ("aadhaar 1234 5678 9012", "1234 5678 9012"),
        ("password=hunter2", "hunter2"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ],
)
def test_redact_text_removes_sensitive_values(raw: str, must_not_contain: str) -> None:
    redacted = redact_text(raw)

    assert must_not_contain not in redacted
    assert "[REDACTED" in redacted


def test_redact_text_keeps_harmless_text() -> None:
    text = "Family of four wants Kerala in December, budget is flexible."

    assert redact_text(text) == text


def test_redact_mapping_handles_nested_values_and_secret_types() -> None:
    payload = {
        "email": "family@example.com",
        "profile": {
            "phone": "+919876543210",
            "passport_no": "M1234567",
            "note": "call 9876543210",
        },
        "secret_key": SecretStr("super-secret"),
        "children": [{"dob": "2018-01-01"}],
        "count": 4,
    }

    redacted = redact_mapping(payload)

    assert redacted["email"] == REDACTED
    assert redacted["profile"]["phone"] == REDACTED
    assert redacted["profile"]["passport_no"] == REDACTED
    assert "9876543210" not in redacted["profile"]["note"]
    assert redacted["secret_key"] == REDACTED
    assert redacted["children"][0]["dob"] == REDACTED
    assert redacted["count"] == 4


def test_filter_redacts_sensitive_log_extras() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="customer created",
        args=(),
        exc_info=None,
    )
    record.email = "family@example.com"
    record.customer_id = "cust_123"
    record.note = "reach me on 9876543210"

    RedactionFilter().filter(record)

    assert record.email == REDACTED
    assert record.customer_id == "cust_123"
    assert "9876543210" not in record.note


def test_json_formatter_has_request_id_and_no_pii() -> None:
    token = set_request_id("req-test-1234")
    try:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="travelos.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="contact family@example.com about booking %s",
            args=("BK-123",),
            exc_info=None,
        )
        record.email = "family@example.com"

        payload = json.loads(formatter.format(record))
    finally:
        reset_request_id(token)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "travelos.test"
    assert payload["request_id"] == "req-test-1234"
    assert payload["email"] == REDACTED
    assert "family@example.com" not in json.dumps(payload)
    assert "BK-123" in payload["message"]


def test_json_formatter_serialises_exceptions() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="travelos.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=True,
        )
        record.exc_info = __import__("sys").exc_info()

    payload = json.loads(formatter.format(record))

    assert "ValueError: boom" in payload["exception"]


def test_text_formatter_redacts() -> None:
    formatter = TextFormatter()
    record = logging.LogRecord(
        name="travelos.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="email family@example.com",
        args=(),
        exc_info=None,
    )

    rendered = formatter.format(record)

    assert "family@example.com" not in rendered
    assert "[REDACTED_EMAIL]" in rendered


def test_configure_logging_is_idempotent_and_validates_level() -> None:
    configure_logging("INFO", json_output=True)
    configure_logging("DEBUG", json_output=True)

    travelos_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_travelos_handler", False)
    ]
    assert len(travelos_handlers) == 1
    assert get_request_id() is None

    with pytest.raises(ValueError, match="Unknown log level"):
        configure_logging("LOUD")
