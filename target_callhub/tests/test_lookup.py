"""Unit tests for contact lookup and unified field mapping."""

from __future__ import annotations

from target_callhub.client import _CallHubCache
from target_callhub.contact_lookup import ContactLookupMixin
from target_callhub.unified_mapping import (
    contact_lookup_value,
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
