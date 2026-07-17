"""CallHub target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue

from target_callhub.sinks import ContactsSink


class TargetCallhub(TargetHotglue):
    """Target for CallHub unified Contacts."""

    SINK_TYPES = [
        ContactsSink,
    ]
    name = "target-callhub"
    alerting_level = AlertingLevel.ERROR

    config_jsonschema = th.PropertiesList(
        th.Property(
            "api_token",
            th.StringType,
            required=True,
            description="CallHub API token from account settings.",
        ),
        th.Property(
            "api_base_url",
            th.StringType,
            required=True,
            description="CallHub API base URL for the account region.",
        ),
        th.Property(
            "only_upsert_empty_fields",
            th.BooleanType,
            description="Only update CallHub fields that are currently empty.",
        ),
        th.Property(
            "lookup_method",
            th.StringType,
            description='Lookup strategy: "sequential" or "all". Defaults to sequential.',
        ),
        th.Property(
            "lookup_fields",
            th.ObjectType(),
            description='Per-stream lookup fields, e.g. {"Contacts": ["id", "email"]}.',
        ),
    ).to_dict()


if __name__ == "__main__":
    TargetCallhub.cli()
