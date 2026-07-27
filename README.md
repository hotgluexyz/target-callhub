# target-callhub

`target-callhub` is a Singer target for [CallHub](https://callhub.io), built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) for Singer Targets.

## Installation

```bash
pip install target-callhub
```

Or from source:

```bash
pip install -e .
```

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `api_token` | Yes | CallHub API token from account settings |
| `api_base_url` | Yes | Regional API base URL, e.g. `https://api-na1.callhub.io` |
| `only_upsert_empty_fields` | No | Only update CallHub fields that are currently empty |
| `lookup_fields` | No | Per-stream lookup fields, e.g. `{"Contacts": ["id", "email"]}` |
| `lookup_method` | No | `sequential` (default) or `all` |

Example `config.json`:

```json
{
  "api_token": "YOUR_API_TOKEN",
  "api_base_url": "https://api-na1.callhub.io",
  "lookup_fields": {
    "Contacts": ["id", "email"]
  },
  "lookup_method": "sequential",
  "only_upsert_empty_fields": false
}
```

### Lookup fields

CallHub has no server-side match API, so upserts resolve existing contacts from a full contact cache loaded once per job. Configure `lookup_fields` and `lookup_method` (`sequential` or `all`) to control how records are matched before create vs update.

Use unified CRM field names in `lookup_fields`. The target maps them to CallHub contact fields when reading from incoming records and when comparing against the contact cache.

Supported examples:

| Unified field | CallHub field |
| --- | --- |
| `id` | `id` |
| `email` | `email` |
| `contact`, `mobile` | derived from `phone_numbers` |
| `website` | `company_website` |
| `title` | `job_title` |
| `city`, `state`, `country`, `postal_code`, `address` | first address in `addresses` |

Any other unified field that shares the same name on the CallHub contact payload (for example `first_name` or `last_name`) also works.

## Source Authentication and Authorization

CallHub uses a static API token. Create one in the CallHub UI under account settings and use it with the `Authorization: Token <token>` header.

## Supported Streams

| Stream | Description |
| --- | --- |
| `Contacts` | Unified contacts sink with tags, phonebooks (lists), custom fields, and upsert support |

## Usage

```bash
cat sample_payload/data.singer | target-callhub --config .secrets/config.json
```

## Developer Resources

Create `.secrets/config.json` with your sandbox credentials:

```json
{
  "api_token": "YOUR_API_TOKEN",
  "api_base_url": "https://api-na1.callhub.io"
}
```

Run lint and e2e tests (e2e needs the config above; payloads live in `sample_payload/`):

```bash
python -m venv .venv
.venv/bin/pip install -e . ruff pytest requests
.venv/bin/ruff check .
.venv/bin/pytest target_callhub/tests/test_e2e.py -v
```
