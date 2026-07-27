"""Unified CRM field names mapped to CallHub contact API fields."""

from __future__ import annotations

from typing import Any

# Unified field name -> CallHub contact payload key.
UNIFIED_TO_CALLHUB: dict[str, str] = {
    "website": "company_website",
    "title": "job_title",
}

# Standard CallHub contact payload keys (not custom field definitions).
CALLHUB_NATIVE_CONTACT_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "middle_name",
        "email",
        "salutation",
        "description",
        "contact",
        "mobile",
        "company_name",
        "company_website",
        "job_title",
        "address",
        "street_address_line1",
        "city",
        "state",
        "zipcode",
        "country_code",
    },
)

# Address sub-fields: unified address dict key -> CallHub contact key.
ADDRESS_FIELD_TO_CALLHUB: dict[str, str] = {
    "line1": "address",
    "city": "city",
    "state": "state",
    "postal_code": "zipcode",
    "postalCode": "zipcode",
    "country": "country_code",
}


def callhub_field_name(unified_field: str) -> str:
    """Return the CallHub contact key used for a unified lookup field."""
    if unified_field in ADDRESS_FIELD_TO_CALLHUB:
        return ADDRESS_FIELD_TO_CALLHUB[unified_field]
    for callhub_key in ADDRESS_FIELD_TO_CALLHUB.values():
        if unified_field == callhub_key:
            return callhub_key
    return UNIFIED_TO_CALLHUB.get(unified_field, unified_field)


NON_DIALABLE_PHONE_TYPES = {"fax", "pager"}


def extract_phones(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Map unified phone_numbers to CallHub contact and mobile values."""
    contact_phone = None
    mobile = None
    for phone in record.get("phone_numbers") or []:
        if not isinstance(phone, dict):
            continue
        number = phone.get("number")
        if not number:
            continue
        phone_type = str(phone.get("type") or "").lower()
        if phone_type in NON_DIALABLE_PHONE_TYPES:
            continue
        if phone_type in {"primary", "home", "work", "phone"} and not contact_phone:
            contact_phone = str(number)
        elif phone_type == "mobile" and not mobile:
            mobile = str(number)
        elif not contact_phone:
            contact_phone = str(number)
    if not mobile and contact_phone:
        mobile = contact_phone
    if not contact_phone and mobile:
        contact_phone = mobile
    return contact_phone, mobile


def normalize_phones(
    contact_phone: str | None,
    mobile: str | None,
) -> tuple[str | None, str | None]:
    """Mirror contact and mobile when only one phone value is present."""
    if not mobile and contact_phone:
        mobile = contact_phone
    if not contact_phone and mobile:
        contact_phone = mobile
    return contact_phone, mobile


def build_contact_payload(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map a unified contact record to a CallHub contact payload."""
    contact_phone, mobile = extract_phones(record)
    contact_phone, mobile = normalize_phones(contact_phone, mobile)
    payload: dict[str, Any] = {
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "email": record.get("email"),
        "company_name": record.get("company_name"),
    }

    address = _first_address(record)
    if address:
        payload["address"] = address.get("line1")
        payload["street_address_line1"] = address.get("line1")
        for address_key, callhub_key in ADDRESS_FIELD_TO_CALLHUB.items():
            if address_key in {"line1", "postalCode"}:
                continue
            payload[callhub_key] = address.get(address_key)
        payload["zipcode"] = address.get("postal_code") or address.get("postalCode")

    payload["contact"] = contact_phone
    payload["mobile"] = mobile
    payload["company_website"] = record.get("website")
    payload["job_title"] = record.get("title")

    custom_field_names: list[str] = []
    for custom_field in record.get("custom_fields") or []:
        if not isinstance(custom_field, dict):
            continue
        field_name = custom_field.get("name")
        if not field_name:
            continue
        payload[field_name] = custom_field.get("value")
        if field_name not in CALLHUB_NATIVE_CONTACT_FIELDS:
            custom_field_names.append(field_name)

    return payload, custom_field_names


def _first_address(record: dict[str, Any]) -> dict[str, Any]:
    addresses = record.get("addresses") or []
    if addresses and isinstance(addresses[0], dict):
        return addresses[0]
    return {}


def unified_lookup_value(record: dict[str, Any], unified_field: str) -> Any:
    """Read a lookup value from a unified contact record."""
    if unified_field == "id":
        return record.get("id")
    if unified_field == "email" and record.get("email"):
        return str(record["email"]).strip().lower()
    if unified_field == "contact":
        return extract_phones(record)[0]
    if unified_field == "mobile":
        return extract_phones(record)[1]

    address = _first_address(record)
    for address_key, callhub_key in ADDRESS_FIELD_TO_CALLHUB.items():
        if unified_field == callhub_key or unified_field == address_key:
            return address.get(address_key) or record.get(unified_field)

    return record.get(unified_field)


def contact_lookup_value(contact: dict[str, Any], unified_field: str) -> Any:
    """Read a comparable lookup value from a cached CallHub contact."""
    if unified_field == "id":
        return contact.get("id")
    if unified_field == "email" and contact.get("email"):
        return str(contact["email"]).strip().lower()
    if unified_field in {"contact", "mobile"}:
        return contact.get(unified_field)

    callhub_key = callhub_field_name(unified_field)
    value = contact.get(callhub_key)
    if unified_field in {"address", "line1"} and value in (None, ""):
        value = contact.get("street_address_line1")
    return value


def values_match(unified_field: str, expected: Any, actual: Any) -> bool:
    """Compare lookup values from a unified record and a CallHub contact."""
    if actual in (None, ""):
        return False
    if expected in (None, ""):
        return False
    if unified_field == "email":
        return str(actual).strip().lower() == str(expected).strip().lower()
    return str(actual) == str(expected)
