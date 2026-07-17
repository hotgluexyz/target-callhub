"""CallHub target sink classes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from hotglue_etl_exceptions import InvalidPayloadError

from target_callhub.client import CallHubSink
from target_callhub.unified_mapping import build_contact_payload


class ContactsSink(CallHubSink):
    """Unified Contacts sink for CallHub."""

    name = "Contacts"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_tags: List[str] = []
        self._pending_lists: List[Dict[str, str]] = []

    @staticmethod
    def _normalize_subscription_status(status: Optional[str]) -> str:
        if not status:
            return "subscribed"
        normalized = str(status).strip().lower()
        if normalized in {"unsubscribed", "unsubscribe", "opt_out", "opt-out"}:
            return "unsubscribed"
        return "subscribed"

    def parse_list_entries(self, record: Dict[str, Any]) -> List[Dict[str, str]]:
        """Normalize unified lists into phonebook actions with subscription status."""
        entries: List[Dict[str, str]] = []
        default_status = self._normalize_subscription_status(record.get("subscribe_status"))
        for item in record.get("lists") or []:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    entries.append({"name": name, "subscription_status": default_status})
            elif isinstance(item, dict):
                name = (item.get("name") or item.get("list_name") or "").strip()
                if not name:
                    continue
                status = item.get("subscription_status") or item.get("subscribe_status")
                entries.append(
                    {
                        "name": name,
                        "subscription_status": self._normalize_subscription_status(
                            status or default_status,
                        ),
                    },
                )
        return entries

    def preprocess_record(self, record: Dict[str, Any], context: dict) -> dict:
        """Map, lookup, and merge a unified record before writing to CallHub."""
        self._pending_tags = [
            str(tag).strip()
            for tag in (record.get("tags") or [])
            if tag is not None and str(tag).strip()
        ]
        self._pending_lists = self.parse_list_entries(record)

        payload, custom_field_names = build_contact_payload(record)

        matching_contact = self.find_matching_contact(record)
        only_upsert_empty_fields = bool(self.config.get("only_upsert_empty_fields"))

        if matching_contact:
            payload["contact"] = payload.get("contact") or matching_contact.get("contact")
            payload["mobile"] = payload.get("mobile") or matching_contact.get("mobile")
            if only_upsert_empty_fields:
                payload = self.merge_empty_fields(matching_contact, payload)
            matching_id = matching_contact.get("id")
            if matching_id is not None:
                payload["_callhub_id"] = matching_id

        if not payload.get("contact"):
            raise InvalidPayloadError(
                "CallHub requires a phone number. Provide phone_numbers with at least one number.",
            )

        self.prepare_custom_field_payload(payload, custom_field_names)

        return self.clean_null_values(payload)

    @staticmethod
    def _collect_tags_to_apply(
        cached_contact: Dict[str, Any],
        pending_tags: List[str],
    ) -> List[str]:
        """Merge cached and pending tag names, preserving order and deduplicating."""
        cached_tag_names = []
        for tag in cached_contact.get("tags") or []:
            if isinstance(tag, dict) and tag.get("name"):
                cached_tag_names.append(tag["name"])
            elif isinstance(tag, str) and tag.strip():
                cached_tag_names.append(tag.strip())
        tags_to_apply: List[str] = []
        seen_tags: set[str] = set()
        for tag_name in cached_tag_names + pending_tags:
            lowered = tag_name.lower()
            if lowered not in seen_tags:
                tags_to_apply.append(tag_name)
                seen_tags.add(lowered)
        return tags_to_apply

    def upsert_record(self, record: dict, context: dict):
        """Create or update a contact, then apply tags and phonebook membership."""
        state_dict: Dict[str, Any] = {}
        contact_id = record.pop("_callhub_id", None)
        cached_before = self._cache.contacts_by_id.get(str(contact_id), {}) if contact_id else {}
        tags_to_apply = self._collect_tags_to_apply(cached_before, self._pending_tags)
        method = "PUT" if contact_id else "POST"
        endpoint = f"v1/contacts/{contact_id}/" if contact_id else "v1/contacts/"

        response = self.request_api(method, endpoint=endpoint, request_data=record)
        contact = response.json()
        contact_id = contact.get("id")
        self._store_contact_in_cache(contact)

        if tags_to_apply:
            self._pending_tags = tags_to_apply
            self.resolve_tags(contact)
        if self._pending_lists:
            self.resolve_phonebooks(contact)

        state_dict["success"] = True
        if method == "PUT":
            state_dict["is_updated"] = True
        return contact_id, response.ok, state_dict

    def resolve_tags(self, contact: Dict[str, Any]) -> None:
        """Apply pending tags to a contact, re-applying cached tags cleared by PUT."""
        contact_id = contact.get("id")
        if contact_id is None:
            return
        tag_ids: List[str] = []
        for tag_name in self._pending_tags:
            tag = self.get_or_create_tag(tag_name)
            tag_id = tag.get("id")
            if tag_id is not None:
                tag_ids.append(str(tag_id))
        if not tag_ids:
            return
        self.request_api(
            "PATCH",
            endpoint=f"v2/contacts/{contact_id}/taggings/",
            request_data={"tags": tag_ids},
        )
        refreshed = self._fetch_contact_by_id(str(contact_id))
        if refreshed:
            contact.update(refreshed)

    def resolve_phonebooks(self, contact: Dict[str, Any]) -> None:
        """Add or remove phonebook membership based on parsed list entries."""
        contact_id = contact.get("id")
        if contact_id is None:
            return
        current_membership = self.parse_phonebook_ids(contact)
        for list_entry in self._pending_lists:
            if list_entry["subscription_status"] == "unsubscribed":
                phonebook = self.get_phonebook(list_entry["name"])
                if not phonebook:
                    continue
            else:
                phonebook = self.get_or_create_phonebook(list_entry["name"])
            phonebook_id = phonebook.get("id")
            if phonebook_id is None:
                continue
            phonebook_id = str(phonebook_id)
            if list_entry["subscription_status"] == "unsubscribed":
                if phonebook_id in current_membership:
                    self.request_api(
                        "DELETE",
                        endpoint=f"v1/phonebooks/{phonebook_id}/contacts/",
                        request_data={"contact_ids": [str(contact_id)]},
                    )
                    current_membership.discard(phonebook_id)
            elif phonebook_id not in current_membership:
                self.request_api(
                    "POST",
                    endpoint=f"v1/phonebooks/{phonebook_id}/contacts/",
                    request_data={"contact_ids": [str(contact_id)]},
                )
                current_membership.add(phonebook_id)
        refreshed = self._fetch_contact_by_id(str(contact_id))
        if refreshed:
            contact.update(refreshed)
