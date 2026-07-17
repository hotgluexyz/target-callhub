"""Custom field helpers for target-callhub."""

from __future__ import annotations

import json
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


def custom_field_values_by_name(
    contact: Dict[str, Any],
    definitions_by_name: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Return custom field values from a contact payload keyed by field name."""
    raw = contact.get("custom_fields")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            values_by_id = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    elif isinstance(raw, dict):
        values_by_id = raw
    else:
        return {}

    id_to_name: Dict[str, str] = {}
    seen_ids: set[str] = set()
    for definition in definitions_by_name.values():
        field_id = definition.get("id")
        if field_id is None:
            continue
        field_id_str = str(field_id)
        if field_id_str in seen_ids:
            continue
        seen_ids.add(field_id_str)
        name = definition.get("name")
        if name:
            id_to_name[field_id_str] = name

    by_name: Dict[str, Any] = {}
    for field_id, value in values_by_id.items():
        name = id_to_name.get(str(field_id))
        if name is not None:
            by_name[name] = value
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
