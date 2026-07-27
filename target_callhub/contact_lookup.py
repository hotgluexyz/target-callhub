"""Contact cache and client-side lookup for CallHub upserts."""

from __future__ import annotations

from typing import Any

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_callhub.unified_mapping import (
    contact_lookup_value,
    unified_lookup_value,
    values_match,
)

DEFAULT_LOOKUP_FIELDS = ["id", "email"]


def _merge_cached_contact(
    previous: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Overlay a partial API response onto a cached contact without dropping omitted fields."""
    merged = dict(previous)
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value
    return merged


class ContactLookupMixin:
    """Load contacts once per job and resolve upsert matches from the in-memory cache."""

    @property
    def lookup_method(self) -> str:
        method = (self.config.get("lookup_method") or "sequential").lower()
        return method if method in {"sequential", "all"} else "sequential"

    def ensure_contacts_loaded(self) -> None:
        """Load all contacts once per job into shared id and email indexes."""
        if self._cache.contacts_loaded:
            return
        self.logger.info("Loading all contacts into cache")
        for contact in self._paginate("v1/contacts/"):
            self._store_contact_in_cache(contact)
        self._log_duplicate_emails()
        self._cache.contacts_loaded = True
        self.logger.info("Loaded %s contacts into cache", len(self._cache.contacts_by_id))

    def _store_contact_in_cache(self, contact: dict[str, Any]) -> None:
        """Update the in-memory contact indexes from a contact payload."""
        contact_id = contact.get("id")
        if contact_id is None:
            return
        contact_id_str = str(contact_id)
        previous = self._cache.contacts_by_id.get(contact_id_str)
        if previous:
            contact = _merge_cached_contact(previous, contact)
        self._cache.contacts_by_id[contact_id_str] = contact

        email = contact.get("email")
        if previous and previous.get("email"):
            old_key = str(previous["email"]).strip().lower()
            new_key = str(email).strip().lower() if email else None
            if old_key != new_key:
                remaining = [
                    existing
                    for existing in self._cache.contacts_by_email.get(old_key, [])
                    if str(existing.get("id")) != contact_id_str
                ]
                if remaining:
                    self._cache.contacts_by_email[old_key] = remaining
                else:
                    self._cache.contacts_by_email.pop(old_key, None)

        if not email:
            return
        key = str(email).strip().lower()
        bucket = self._cache.contacts_by_email.setdefault(key, [])
        for index, existing in enumerate(bucket):
            if str(existing.get("id")) == contact_id_str:
                bucket[index] = contact
                return
        bucket.append(contact)

    def _log_duplicate_emails(self) -> None:
        """Warn once per job about emails attached to multiple CallHub contacts."""
        for email, contacts in self._cache.contacts_by_email.items():
            if len(contacts) <= 1:
                continue
            ids = [contact.get("id") for contact in contacts]
            self.logger.warning(
                "Duplicate email '%s' found on CallHub contacts with ids: %s",
                email,
                ids,
            )

    def _warn_duplicate_lookup(
        self,
        email: str,
        contacts: list[dict[str, Any]],
        chosen: dict[str, Any],
    ) -> None:
        """Warn when a lookup hits multiple contacts for the same email."""
        key = email.strip().lower()
        if key in self._duplicate_emails_logged:
            return
        ids = [contact.get("id") for contact in contacts]
        self.logger.warning(
            "Lookup matched duplicate email '%s'; using contact id %s. Other ids: %s",
            email,
            chosen.get("id"),
            ids,
        )
        self._duplicate_emails_logged.add(key)

    def get_lookup_fields(self) -> list[str]:
        """Return configured lookup fields for this stream, defaulting to id then email."""
        lookup_config = self.config.get("lookup_fields") or {}
        fields = lookup_config.get(self.name) or lookup_config.get("Contacts")
        if fields is None:
            fields = DEFAULT_LOOKUP_FIELDS
        if isinstance(fields, str):
            return [fields]
        return list(fields)

    def _pick_best_duplicate(
        self,
        contacts: list[dict[str, Any]],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose one contact when multiple records share a lookup value."""
        record_id = record.get("id")
        if record_id is not None:
            for contact in contacts:
                if str(contact.get("id")) == str(record_id):
                    return contact
        sorted_contacts = sorted(
            contacts,
            key=lambda contact: contact.get("created_date") or "",
            reverse=True,
        )
        return sorted_contacts[0]

    def _record_field_value(self, record: dict[str, Any], field: str) -> Any:
        return unified_lookup_value(record, field)

    def _contact_matches_value(
        self,
        contact: dict[str, Any],
        field: str,
        expected: Any,
    ) -> bool:
        """Return whether a cached contact matches a unified lookup field value."""
        actual = contact_lookup_value(contact, field)
        return values_match(field, expected, actual)

    def _lookup_by_field(
        self,
        record: dict[str, Any],
        field: str,
    ) -> dict[str, Any] | None:
        """Find a cached contact using a single lookup field."""
        value = self._record_field_value(record, field)
        if value in (None, ""):
            return None

        if field == "id":
            return self._cache.contacts_by_id.get(str(value))

        if field == "email":
            matches = self._cache.contacts_by_email.get(str(value).strip().lower(), [])
            if not matches:
                return None
            chosen = self._pick_best_duplicate(matches, record)
            if len(matches) > 1:
                self._warn_duplicate_lookup(str(value), matches, chosen)
            return chosen

        matches: list[dict[str, Any]] = []
        for contact in self._cache.contacts_by_id.values():
            if self._contact_matches_value(contact, field, value):
                matches.append(contact)
        if not matches:
            return None
        chosen = self._pick_best_duplicate(matches, record)
        if len(matches) > 1:
            ids = [contact.get("id") for contact in matches]
            self.logger.warning(
                "Lookup matched multiple contacts for field '%s'; using contact id %s. Other ids: %s",
                field,
                chosen.get("id"),
                ids,
            )
        return chosen

    def _lookup_by_all_fields(
        self,
        record: dict[str, Any],
        fields: list[str],
    ) -> dict[str, Any] | None:
        """Find a cached contact that matches every configured lookup field."""
        for field in fields:
            if self._record_field_value(record, field) in (None, ""):
                return None

        candidates = list(self._cache.contacts_by_id.values())
        for field in fields:
            expected = self._record_field_value(record, field)
            candidates = [
                contact
                for contact in candidates
                if self._contact_matches_value(contact, field, expected)
            ]
            if not candidates:
                return None

        if len(candidates) > 1:
            email = record.get("email")
            chosen = self._pick_best_duplicate(candidates, record)
            if email:
                self._warn_duplicate_lookup(str(email), candidates, chosen)
            else:
                ids = [contact.get("id") for contact in candidates]
                self.logger.warning(
                    "Lookup matched multiple contacts for fields %s; using contact id %s. Other ids: %s",
                    fields,
                    chosen.get("id"),
                    ids,
                )
            return chosen
        return self._pick_best_duplicate(candidates, record)

    def find_matching_contact(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve an existing contact from the cache using configured lookup fields."""
        self.ensure_contacts_loaded()
        fields = self.get_lookup_fields()
        if not fields:
            return None
        if self.lookup_method == "all":
            return self._lookup_by_all_fields(record, fields)

        for field in fields:
            match = self._lookup_by_field(record, field)
            if match:
                self.logger.info(
                    "Found existing contact id %s via lookup field '%s'",
                    match.get("id"),
                    field,
                )
                return match
        return None

    def _fetch_contact_by_id(self, contact_id: str) -> dict[str, Any] | None:
        """Fetch a contact by id when it is missing from the cache."""
        try:
            response = self.request_api("GET", endpoint=f"v1/contacts/{contact_id}/")
        except FatalAPIError:
            return None
        contact = response.json()
        self._store_contact_in_cache(contact)
        return contact
