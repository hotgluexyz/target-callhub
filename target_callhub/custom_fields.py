"""Custom field helpers for target-callhub."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def custom_field_definitions_by_name(
    definitions: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Index custom field definitions by name, keeping the first occurrence."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for definition in definitions:
        name = definition.get("name")
        if name and name not in by_name:
            by_name[name] = definition
    return by_name


def _coerce_number_value(value: Any) -> Any:
    try:
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str) and "." in value:
            return float(value)
        return int(value)
    except (TypeError, ValueError):
        return value


def _coerce_boolean_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)


def coerce_custom_field_value(value: Any, field_type: Optional[str]) -> Any:
    """Coerce a value to the type expected by a CallHub custom field definition."""
    normalized_type = (field_type or "text").lower()
    if value is None:
        return None
    if normalized_type == "number":
        return _coerce_number_value(value)
    if normalized_type == "boolean":
        return _coerce_boolean_value(value)
    return value
