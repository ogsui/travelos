"""A deliberately small JSON Schema validator for LLM output.

The gateway validates every structured response against the schema declared by
the prompt. We implement the subset of JSON Schema the prompts actually use
rather than pulling in a full validator: the behaviour is deterministic, easy to
audit, and no schema keyword can silently fall back to ``Any``.

Supported keywords: ``type``, ``properties``, ``required``,
``additionalProperties`` (bool or schema), ``items``, ``enum``, ``const``,
``minLength``, ``maxLength``, ``pattern``, ``minimum``, ``maximum``,
``minItems``, ``maxItems``, ``anyOf``, ``oneOf``, ``allOf`` and ``$ref`` to
``#/$defs/...``. Unknown keywords are ignored (JSON Schema's default).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


def validate_json(value: object, schema: Mapping[str, Any], *, path: str = "$") -> list[str]:
    """Return a list of human-readable validation errors (empty means valid)."""

    return _validate(value, schema, schema, path)


def parse_json_output(text: str) -> JsonValue | None:
    """Extract the first JSON value from model output, tolerating code fences."""

    if not text or not text.strip():
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        return cast(JsonValue, json.loads(candidate))
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict | list):
            return parsed
    return None


def _validate(
    value: object,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
) -> list[str]:
    if "$ref" in schema:
        target = _resolve_ref(str(schema["$ref"]), root)
        if target is None:
            return [f"{path}: unresolved schema reference {schema['$ref']!r}"]
        return _validate(value, target, root, path)

    errors: list[str] = []

    for keyword in ("allOf", "anyOf", "oneOf"):
        parts = schema.get(keyword)
        if not isinstance(parts, list):
            continue
        branch_errors = [
            _validate(value, part, root, path) for part in parts if isinstance(part, Mapping)
        ]
        if keyword == "allOf":
            for failure in branch_errors:
                errors.extend(failure)
        elif keyword == "anyOf" and not any(not failure for failure in branch_errors):
            errors.append(f"{path}: does not match any allowed shape")
        elif keyword == "oneOf":
            matches = sum(1 for failure in branch_errors if not failure)
            if matches != 1:
                errors.append(f"{path}: must match exactly one allowed shape (matched {matches})")

    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        return [*errors, f"{path}: expected {_type_name(expected)}, got {_json_type(value)}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        errors.append(f"{path}: must be one of {allowed!r}")

    if isinstance(value, str):
        errors.extend(_validate_string(value, schema, path))
    if isinstance(value, bool):
        pass
    elif isinstance(value, int | float):
        errors.extend(_validate_number(value, schema, path))
    if isinstance(value, list):
        errors.extend(_validate_array(value, schema, root, path))
    if isinstance(value, dict):
        errors.extend(_validate_object(value, schema, root, path))
    return errors


def _validate_string(value: str, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.append(f"{path}: must be at least {minimum} characters")
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.append(f"{path}: must be at most {maximum} characters")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            if re.search(pattern, value) is None:
                errors.append(f"{path}: does not match pattern {pattern!r}")
        except re.error:
            errors.append(f"{path}: invalid schema pattern {pattern!r}")
    return errors


def _validate_number(value: int | float, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        errors.append(f"{path}: must be >= {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        errors.append(f"{path}: must be <= {maximum}")
    return errors


def _validate_array(
    value: list[Any],
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.append(f"{path}: must have at least {minimum} items")
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.append(f"{path}: must have at most {maximum} items")
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            errors.extend(_validate(item, items, root, f"{path}[{index}]"))
    return errors


def _validate_object(
    value: dict[str, Any],
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}: missing required property {key!r}")

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, Mapping):
                errors.extend(_validate(value[key], child_schema, root, f"{path}.{key}"))

    additional = schema.get("additionalProperties", True)
    if additional is False:
        known = set(properties) if isinstance(properties, Mapping) else set()
        for key in value:
            if key not in known:
                errors.append(f"{path}: unexpected property {key!r}")
    elif isinstance(additional, Mapping):
        known = set(properties) if isinstance(properties, Mapping) else set()
        for key, item in value.items():
            if key not in known:
                errors.extend(_validate(item, additional, root, f"{path}.{key}"))
    return errors


def _resolve_ref(reference: str, root: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: object = root
    for part in reference[2:].split("/"):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, Mapping) else None


def _type_matches(value: object, expected: object) -> bool:
    if isinstance(expected, list):
        return any(_type_matches(value, item) for item in expected)
    if not isinstance(expected, str):
        return True
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _type_name(expected: object) -> str:
    if isinstance(expected, list):
        return " or ".join(str(item) for item in expected)
    return str(expected)


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
