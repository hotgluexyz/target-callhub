"""CallHub target base sink with HTTP, entity caches, and payload helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError, RetriableAPIError
from hotglue_singer_sdk.plugin_base import PluginBase
from hotglue_singer_sdk.target_sdk.client import HotglueSink

from target_callhub.contact_lookup import ContactLookupMixin
from target_callhub.custom_fields import (
    coerce_custom_field_value,
    custom_field_definitions_by_name,
    custom_field_values_by_name,
)

PHONEBOOK_URL_RE = re.compile(r"/phonebooks/(\d+)/?$")


@dataclass
class _CallHubCache:
    contacts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    contacts_by_email: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    contacts_loaded: bool = False
    phonebooks_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    phonebooks_loaded: bool = False
    tags_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags_loaded: bool = False
    custom_fields_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_fields_loaded: bool = False


class CallHubSink(ContactLookupMixin, HotglueSink):
    """Base sink for CallHub API interactions."""

    page_size = 1000  # CallHub caps responses at 1000 contacts per page.

    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: dict,
        key_properties: list[str] | None,
    ) -> None:
        super().__init__(target, stream_name, schema, key_properties)
        self._cache = _CallHubCache()
        self._duplicate_emails_logged: set[str] = set()

    @property
    def base_url(self) -> str:
        return self.config["api_base_url"].rstrip("/") + "/"

    @property
    def endpoint(self) -> str:
        return "v1/contacts/"

    @property
    def default_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.config['api_token']}",
        }

    def validate_response(self, response: requests.Response) -> None:
        """Map CallHub HTTP status codes to hotglue exception types."""
        if response.status_code in {401, 403}:
            raise InvalidCredentialsError(response.text or response.reason)
        if response.status_code == 400:
            raise InvalidPayloadError(response.text or response.reason)
        if response.status_code in [429] or 500 <= response.status_code < 600:
            raise RetriableAPIError(self.response_error_message(response), response)
        if 400 <= response.status_code < 500:
            raise FatalAPIError(response.text or response.reason)
        super().validate_response(response)

    def _paginate(self, path: str) -> list[dict[str, Any]]:
        """Fetch all pages from a CallHub list endpoint."""
        results: list[dict[str, Any]] = []
        page = 1
        while page:
            response = self.request_api(
                "GET",
                endpoint=path,
                params={"page": page, "page_size": self.page_size},
            )
            payload = response.json()
            if isinstance(payload, list):
                return payload
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
            if not next_url:
                break
            page_values = parse_qs(urlparse(next_url).query).get("page")
            if not page_values:
                break
            try:
                page = int(page_values[0])
            except (TypeError, ValueError):
                break
        return results

    @staticmethod
    def _entity_name(entity: dict[str, Any]) -> str | None:
        """Return the display name for a tag, phonebook, or custom field definition."""
        name = entity.get("name")
        if name:
            return str(name)
        tag = entity.get("tag")
        if tag:
            return str(tag)
        return None

    @staticmethod
    def _register_named_entity(
        cache: dict[str, dict[str, Any]],
        entity: dict[str, Any],
    ) -> None:
        """Store an entity under its display name and lowercase alias."""
        name = CallHubSink._entity_name(entity)
        if not name:
            return
        cache[name] = entity
        cache[name.lower()] = entity

    @staticmethod
    def _get_named_entity(
        cache: dict[str, dict[str, Any]],
        name: str,
    ) -> dict[str, Any] | None:
        """Look up a cached entity by exact or case-insensitive name."""
        trimmed = name.strip()
        return cache.get(trimmed) or cache.get(trimmed.lower())

    def ensure_phonebooks_loaded(self) -> None:
        """Lazy-load phonebooks when list membership is first needed."""
        if self._cache.phonebooks_loaded:
            return
        self.logger.info("Loading phonebooks into cache")
        for phonebook in self._paginate("v1/phonebooks/"):
            self._register_named_entity(self._cache.phonebooks_by_name, phonebook)
        self._cache.phonebooks_loaded = True

    def ensure_tags_loaded(self) -> None:
        """Lazy-load tags when tag assignment is first needed."""
        if self._cache.tags_loaded:
            return
        self.logger.info("Loading tags into cache")
        for tag in self._paginate("v2/tags/"):
            self._register_named_entity(self._cache.tags_by_name, tag)
        self._cache.tags_loaded = True

    def ensure_custom_fields_loaded(self) -> None:
        """Lazy-load custom field definitions when custom fields are first needed."""
        if self._cache.custom_fields_loaded:
            return
        self.logger.info("Loading custom field definitions into cache")
        definitions = self._paginate("v1/custom_fields/")
        self._cache.custom_fields_by_name = custom_field_definitions_by_name(definitions)
        for name, definition in list(self._cache.custom_fields_by_name.items()):
            self._cache.custom_fields_by_name[name.lower()] = definition
        self._cache.custom_fields_loaded = True

    def get_phonebook(self, name: str) -> dict[str, Any] | None:
        """Return a phonebook by name without creating it."""
        self.ensure_phonebooks_loaded()
        return self._get_named_entity(self._cache.phonebooks_by_name, name)

    def get_or_create_phonebook(self, name: str) -> dict[str, Any]:
        """Return a phonebook by name, creating it when missing."""
        self.ensure_phonebooks_loaded()
        existing = self._get_named_entity(self._cache.phonebooks_by_name, name)
        if existing:
            return existing
        trimmed = name.strip()
        self.logger.info("Creating phonebook '%s'", trimmed)
        response = self.request_api(
            "POST",
            endpoint="v1/phonebooks/",
            request_data={"name": trimmed},
        )
        phonebook = response.json()
        self._register_named_entity(self._cache.phonebooks_by_name, phonebook)
        return phonebook

    def get_or_create_tag(self, name: str) -> dict[str, Any]:
        """Return a tag by name, creating it when missing."""
        self.ensure_tags_loaded()
        existing = self._get_named_entity(self._cache.tags_by_name, name)
        if existing:
            return existing
        trimmed = name.strip()
        self.logger.info("Creating tag '%s'", trimmed)
        response = self.request_api(
            "POST",
            endpoint="v2/tags/",
            request_data={"tag": trimmed},
        )
        tag = response.json()
        self._register_named_entity(self._cache.tags_by_name, tag)
        return tag

    def ensure_custom_field(self, name: str, field_type: str = "text") -> dict[str, Any]:
        """Return a custom field definition by name, creating it when missing."""
        self.ensure_custom_fields_loaded()
        existing = self._get_named_entity(self._cache.custom_fields_by_name, name)
        if existing:
            return existing
        trimmed = name.strip()
        self.logger.info("Creating custom field '%s'", trimmed)
        response = self.request_api(
            "POST",
            endpoint="v1/custom_fields/",
            request_data={"name": trimmed, "field_type": field_type},
        )
        definition = response.json()
        self._register_named_entity(self._cache.custom_fields_by_name, definition)
        return definition

    @staticmethod
    def parse_phonebook_ids(contact: dict[str, Any]) -> set[str]:
        """Extract phonebook ids from the contact payload."""
        ids: set[str] = set()
        for phonebook_ref in contact.get("phonebooks") or []:
            if isinstance(phonebook_ref, dict):
                phonebook_id = phonebook_ref.get("id")
            else:
                match = PHONEBOOK_URL_RE.search(str(phonebook_ref))
                phonebook_id = match.group(1) if match else None
            if phonebook_id is not None:
                ids.add(str(phonebook_id))
        return ids

    def clean_null_values(self, data: Any) -> Any:
        """Remove null and blank values while preserving non-empty nested structures."""
        if not isinstance(data, dict):
            return data
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, dict):
                nested = self.clean_null_values(value)
                if nested:
                    cleaned[key] = nested
            elif isinstance(value, list):
                if value:
                    cleaned[key] = value
            elif value != "":
                cleaned[key] = value
        return cleaned

    def merge_empty_fields(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep existing non-empty values and fill only empty fields from incoming data."""
        self.ensure_custom_fields_loaded()
        existing_custom = custom_field_values_by_name(
            existing,
            self._cache.custom_fields_by_name,
        )
        merged = dict(incoming)
        for key in incoming:
            existing_value = existing.get(key)
            if existing_value in (None, "") and key in existing_custom:
                existing_value = existing_custom[key]
            if existing_value not in (None, ""):
                merged[key] = existing_value
        return merged

    @staticmethod
    def infer_custom_field_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "text"

    def prepare_custom_field_payload(
        self,
        payload: dict[str, Any],
        field_names: list[str],
    ) -> None:
        """Ensure custom fields exist and coerce payload values to their API types."""
        if not field_names:
            return
        self.ensure_custom_fields_loaded()
        for field_name in field_names:
            if field_name not in payload:
                continue
            definition = self.ensure_custom_field(
                field_name,
                self.infer_custom_field_type(payload[field_name]),
            )
            payload[field_name] = coerce_custom_field_value(
                payload[field_name],
                definition.get("field_type"),
            )
