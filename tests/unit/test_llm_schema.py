"""Small JSON Schema validator used for structured LLM output."""

from __future__ import annotations

from travelos.llm.schema import parse_json_output, validate_json

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["book", "cancel"]},
        "travellers": {"type": "integer", "minimum": 1, "maximum": 20},
        "notes": {"type": "string", "maxLength": 10},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "required": ["intent", "travellers"],
    "additionalProperties": False,
}


def test_valid_object_has_no_errors() -> None:
    assert validate_json({"intent": "book", "travellers": 4}, SCHEMA) == []


def test_required_and_type_errors_are_reported() -> None:
    errors = validate_json({"intent": "book", "travellers": "four"}, SCHEMA)

    assert any("travellers" in error and "integer" in error for error in errors)


def test_missing_required_property_is_reported() -> None:
    errors = validate_json({"intent": "book"}, SCHEMA)

    assert any("missing required property" in error for error in errors)


def test_additional_property_is_rejected() -> None:
    errors = validate_json({"intent": "book", "travellers": 2, "tool": "book_flight"}, SCHEMA)

    assert any("unexpected property 'tool'" in error for error in errors)


def test_enum_and_range_are_enforced() -> None:
    errors = validate_json({"intent": "admin", "travellers": 0}, SCHEMA)

    assert any("must be one of" in error for error in errors)
    assert any(">= 1" in error for error in errors)


def test_boolean_is_not_an_integer() -> None:
    errors = validate_json({"intent": "book", "travellers": True}, SCHEMA)

    assert any("integer" in error for error in errors)


def test_array_items_and_length_are_checked() -> None:
    errors = validate_json({"intent": "book", "travellers": 2, "tags": ["a", "b", "c"]}, SCHEMA)

    assert any("at most 2 items" in error for error in errors)


def test_nested_ref_is_supported() -> None:
    schema = {
        "$defs": {"intent": {"type": "string", "enum": ["book"]}},
        "type": "object",
        "properties": {"intent": {"$ref": "#/$defs/intent"}},
        "required": ["intent"],
    }

    assert validate_json({"intent": "book"}, schema) == []
    assert validate_json({"intent": "cancel"}, schema) != []


def test_anyof_accepts_a_matching_shape() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}

    assert validate_json("yes", schema) == []
    assert validate_json(3, schema) == []
    assert validate_json([], schema) != []


def test_parse_json_output_accepts_fences_and_prose() -> None:
    assert parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_output('Here is the result: {"a": 1} thanks') == {"a": 1}


def test_parse_json_output_returns_none_for_garbage() -> None:
    assert parse_json_output("not json at all") is None
