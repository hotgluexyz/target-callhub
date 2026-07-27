"""Unit tests for contact lookup and unified field mapping."""

from __future__ import annotations

from target_callhub.client import _CallHubCache
from target_callhub.contact_lookup import ContactLookupMixin
from target_callhub.custom_fields import coerce_custom_field_value, custom_field_values_by_name
from target_callhub.unified_mapping import (
    build_contact_payload,
    contact_lookup_value,
    extract_phones,
    normalize_phones,
    unified_lookup_value,
    values_match,
)


class _LookupSink(ContactLookupMixin):
    name = "Contacts"

    def __init__(self) -> None:
        self._cache = _CallHubCache()
        self._duplicate_emails_logged: set[str] = set()


def test_contact_lookup_value_maps_address_fields() -> None:
    contact = {
        "city": "Portland",
        "state": "OR",
        "zipcode": "97201",
        "country_code": "US",
        "address": "123 Test Street",
    }
    record = {
        "addresses": [
            {
                "line1": "123 Test Street",
                "city": "Portland",
                "state": "OR",
                "postal_code": "97201",
                "country": "US",
            },
        ],
    }

    for unified_field in ("city", "state", "postal_code", "country", "address"):
        expected = unified_lookup_value(record, unified_field)
        actual = contact_lookup_value(contact, unified_field)
        assert values_match(unified_field, expected, actual), unified_field


def test_store_contact_in_cache_updates_email_index() -> None:
    sink = _LookupSink()
    original = {
        "id": "1",
        "email": "user@example.com",
        "first_name": "Before",
    }
    updated = {
        "id": "1",
        "email": "user@example.com",
        "first_name": "After",
    }

    sink._store_contact_in_cache(original)
    sink._store_contact_in_cache(updated)

    assert sink._cache.contacts_by_id["1"]["first_name"] == "After"
    assert len(sink._cache.contacts_by_email["user@example.com"]) == 1
    assert sink._cache.contacts_by_email["user@example.com"][0]["first_name"] == "After"


def test_store_contact_in_cache_moves_email_index_on_change() -> None:
    sink = _LookupSink()
    sink._store_contact_in_cache(
        {"id": "1", "email": "old@example.com", "first_name": "User"},
    )
    sink._store_contact_in_cache(
        {"id": "1", "email": "new@example.com", "first_name": "User"},
    )

    assert "old@example.com" not in sink._cache.contacts_by_email
    assert len(sink._cache.contacts_by_email["new@example.com"]) == 1
    assert sink._cache.contacts_by_email["new@example.com"][0]["email"] == "new@example.com"


def test_custom_field_values_by_name_parses_blob() -> None:
    definitions = {
        "hg_text_field": {"id": "42", "name": "hg_text_field", "field_type": "text"},
        "hg_text_field".lower(): {"id": "42", "name": "hg_text_field", "field_type": "text"},
    }
    contact = {"custom_fields": '{"42": "keep-me"}'}

    assert custom_field_values_by_name(contact, definitions) == {
        "hg_text_field": "keep-me",
    }


def test_id_lookup_uses_cache_without_api_fetch() -> None:
    sink = _LookupSink()
    sink._cache.contacts_loaded = True
    sink._cache.contacts_by_id["99"] = {"id": "99", "email": "user@example.com"}

    assert sink._lookup_by_field({"id": "external-source-id"}, "id") is None
    assert sink._lookup_by_field({"id": "99"}, "id")["id"] == "99"


def test_custom_fields_routes_native_and_custom_names() -> None:
    payload, custom_names = build_contact_payload(
        {
            "email": "user@example.com",
            "phone_numbers": [{"type": "mobile", "number": "15551234567"}],
            "custom_fields": [
                {"name": "company_website", "value": "https://example.com"},
                {"name": "middle_name", "value": "Alex"},
                {"name": "hg_text_field", "value": "custom-value"},
            ],
        },
    )
    assert payload["company_website"] == "https://example.com"
    assert payload["middle_name"] == "Alex"
    assert payload["hg_text_field"] == "custom-value"
    assert "company_website" not in custom_names
    assert "middle_name" not in custom_names
    assert custom_names == ["hg_text_field"]


def test_top_level_extra_fields_are_ignored() -> None:
    payload, custom_names = build_contact_payload(
        {
            "email": "user@example.com",
            "hg_text_field": "ignored",
            "company_website": "https://example.com",
        },
    )
    assert "hg_text_field" not in payload
    assert payload.get("company_website") != "https://example.com"
    assert custom_names == []


def test_normalize_phones_mirrors_mobile_to_contact() -> None:
    assert normalize_phones(None, "15551234567") == ("15551234567", "15551234567")
    assert normalize_phones("15559876543", None) == ("15559876543", "15559876543")


def test_coerce_boolean_blank_returns_none() -> None:
    assert coerce_custom_field_value("", "boolean") is None
    assert coerce_custom_field_value("  ", "boolean") is None


def test_extract_phones_skips_fax() -> None:
    record = {
        "phone_numbers": [
            {"type": "fax", "number": "15551111111"},
            {"type": "mobile", "number": "15552222222"},
        ],
    }
    assert extract_phones(record) == ("15552222222", "15552222222")


def test_coerce_boolean_unrecognized_string_unchanged() -> None:
    assert coerce_custom_field_value("maybe", "boolean") == "maybe"
    assert coerce_custom_field_value("off", "boolean") is False
