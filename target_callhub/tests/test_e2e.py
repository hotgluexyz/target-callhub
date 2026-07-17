"""End-to-end tests against the CallHub sandbox API.

Requires only a base config at .secrets/config.json with api_token and api_base_url.
Test-specific lookup and feature flags are merged in at runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".secrets" / "config.json"
SAMPLE_PAYLOAD_DIR = REPO_ROOT / "sample_payload"

CREATE_EMAIL = "hotglue-e2e-create-10745@example.com"
UPSERT_EMAIL = "hotglue-e2e-upsert-10745@example.com"
LOOKUP_EMAIL = "hotglue-e2e-lookup-10745@example.com"
UNIFIED_LOOKUP_EMAIL = "hotglue-e2e-unified-lookup-10745@example.com"
EMPTY_FIELDS_EMAIL = "hotglue-e2e-empty-fields-10745@example.com"
CREATE_PHONE = "15551000001"
UPSERT_PHONE = "15551000002"
UNIFIED_LOOKUP_WEBSITE = "https://unified-lookup-10745.example.com"


@dataclass(frozen=True)
class TargetRun:
    result: subprocess.CompletedProcess
    log_path: Path


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        pytest.skip(
            f"Missing sandbox config at {CONFIG_PATH}. "
            "Create it with api_token and api_base_url to run e2e tests.",
        )
    return json.loads(CONFIG_PATH.read_text())


def _write_config(base_config: dict, path: Path, **overrides: Any) -> Path:
    path.write_text(json.dumps({**base_config, **overrides}, indent=2))
    return path


def _auth_headers(config: dict) -> dict:
    return {"Authorization": f"Token {config['api_token']}"}


def _api_base(config: dict) -> str:
    return config["api_base_url"].rstrip("/")


def _find_contacts_by_email(config: dict, email: str) -> list[dict]:
    headers = _auth_headers(config)
    base = _api_base(config)
    matches: list[dict] = []
    page = 1
    while page:
        response = requests.get(
            f"{base}/v1/contacts/",
            headers=headers,
            params={"page": page, "page_size": 100},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        for contact in payload.get("results", []):
            if (contact.get("email") or "").lower() == email.lower():
                matches.append(contact)
        next_url = payload.get("next")
        if not next_url:
            break
        page += 1
    return matches


def _get_contact(config: dict, contact_id: str) -> dict:
    response = requests.get(
        f"{_api_base(config)}/v1/contacts/{contact_id}/",
        headers=_auth_headers(config),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _delete_contact(config: dict, contact_id: str) -> None:
    response = requests.delete(
        f"{_api_base(config)}/v1/contacts/{contact_id}/",
        headers=_auth_headers(config),
        timeout=60,
    )
    if response.status_code not in {204, 404}:
        response.raise_for_status()


def _cleanup_test_contacts(config: dict) -> None:
    for email in {
        CREATE_EMAIL,
        UPSERT_EMAIL,
        LOOKUP_EMAIL,
        UNIFIED_LOOKUP_EMAIL,
        EMPTY_FIELDS_EMAIL,
    }:
        for contact in _find_contacts_by_email(config, email):
            contact_id = contact.get("id")
            if contact_id is not None:
                _delete_contact(config, str(contact_id))


def _run_target(config_path: Path, payload_path: Path, log_path: Path) -> TargetRun:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    with payload_path.open("rb") as payload, log_path.open("wb") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "target_callhub.target",
                "--config",
                str(config_path),
            ],
            stdin=payload,
            stdout=subprocess.PIPE,
            stderr=log,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )
    return TargetRun(result=result, log_path=log_path)


@pytest.fixture(scope="module")
def config() -> dict:
    return _load_config()


@pytest.fixture(scope="module")
def e2e_tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("target-callhub-e2e")


@pytest.fixture(scope="module", autouse=True)
def cleanup_contacts(config: dict):
    _cleanup_test_contacts(config)
    yield
    _cleanup_test_contacts(config)


@pytest.fixture(scope="module")
def target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    return _run_target(
        CONFIG_PATH,
        SAMPLE_PAYLOAD_DIR / "data.singer",
        e2e_tmp / "main-output.log",
    )


@pytest.fixture(scope="module")
def lookup_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "lookup-config.json",
        lookup_fields={"Contacts": ["email"]},
        lookup_method="sequential",
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "lookup-e2e.singer",
        e2e_tmp / "lookup-output.log",
    )


@pytest.fixture(scope="module")
def unified_lookup_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "unified-lookup-config.json",
        lookup_fields={"Contacts": ["website"]},
        lookup_method="sequential",
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "unified-lookup-e2e.singer",
        e2e_tmp / "unified-lookup-output.log",
    )


@pytest.fixture(scope="module")
def empty_fields_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "empty-fields-config.json",
        only_upsert_empty_fields=True,
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "empty-fields-e2e.singer",
        e2e_tmp / "empty-fields-output.log",
    )


def test_target_cli_exits_cleanly(target_run: TargetRun) -> None:
    log_text = target_run.log_path.read_text()
    assert target_run.result.returncode == 0, log_text
    assert "Upsert record error" not in log_text
    assert "Preprocess record error" not in log_text


def test_create_contact_written(config: dict, target_run: TargetRun) -> None:
    matches = _find_contacts_by_email(config, CREATE_EMAIL)
    assert matches, "Expected created contact to exist"
    contact = _get_contact(config, str(matches[0]["id"]))
    assert contact["first_name"] == "Hotglue"
    assert contact["last_name"] == "Create"
    assert contact["contact"] == CREATE_PHONE
    tag_names = {
        tag["name"].lower()
        for tag in contact.get("tags") or []
        if isinstance(tag, dict) and tag.get("name")
    }
    assert "hg-e2e-tag-10745" in tag_names
    phonebook_ids = {
        ref.split("/")[-2]
        for ref in contact.get("phonebooks") or []
        if isinstance(ref, str)
    }
    assert phonebook_ids, "Expected contact to belong to at least one phonebook"


def test_upsert_contact_updated(config: dict, target_run: TargetRun) -> None:
    matches = _find_contacts_by_email(config, UPSERT_EMAIL)
    assert matches, "Expected upserted contact to exist"
    if len(matches) > 1:
        pytest.fail(f"Expected one upsert contact, found duplicates: {[m['id'] for m in matches]}")
    contact = _get_contact(config, str(matches[0]["id"]))
    assert contact["first_name"] == "HotglueUpdated"
    assert contact["last_name"] == "Upsert"
    assert contact["contact"] == UPSERT_PHONE
    tag_names = {
        tag["name"].lower()
        for tag in contact.get("tags") or []
        if isinstance(tag, dict) and tag.get("name")
    }
    assert "hg-e2e-tag-10745" in tag_names
    assert "hg-e2e-tag-second-10745" in tag_names


def test_custom_field_value_written(config: dict, target_run: TargetRun) -> None:
    matches = _find_contacts_by_email(config, UPSERT_EMAIL)
    contact = _get_contact(config, str(matches[0]["id"]))
    custom_fields = contact.get("custom_fields") or ""
    assert "updated-value" in custom_fields or "from-unmapped-field" in custom_fields


def test_lookup_fields_email_upserts_without_duplicates(
    config: dict,
    lookup_target_run: TargetRun,
) -> None:
    log_text = lookup_target_run.log_path.read_text()
    assert lookup_target_run.result.returncode == 0, log_text
    matches = _find_contacts_by_email(config, LOOKUP_EMAIL)
    assert len(matches) == 1, f"Expected one contact, found ids: {[m['id'] for m in matches]}"
    contact = _get_contact(config, str(matches[0]["id"]))
    assert contact["first_name"] == "LookupUpdated"


def test_lookup_fields_support_unified_field_names(
    config: dict,
    unified_lookup_target_run: TargetRun,
) -> None:
    log_text = unified_lookup_target_run.log_path.read_text()
    assert unified_lookup_target_run.result.returncode == 0, log_text
    matches = _find_contacts_by_email(config, UNIFIED_LOOKUP_EMAIL)
    assert len(matches) == 1, f"Expected one contact, found ids: {[m['id'] for m in matches]}"
    contact = _get_contact(config, str(matches[0]["id"]))
    assert contact["first_name"] == "UnifiedLookupUpdated"
    assert contact["company_website"] == UNIFIED_LOOKUP_WEBSITE


def test_only_upsert_empty_fields_preserves_existing_values(
    config: dict,
    empty_fields_target_run: TargetRun,
) -> None:
    log_text = empty_fields_target_run.log_path.read_text()
    assert empty_fields_target_run.result.returncode == 0, log_text
    matches = _find_contacts_by_email(config, EMPTY_FIELDS_EMAIL)
    assert len(matches) == 1
    contact = _get_contact(config, str(matches[0]["id"]))
    assert contact["first_name"] == "KeepMe"
    assert contact["last_name"] == "ShouldFill"
