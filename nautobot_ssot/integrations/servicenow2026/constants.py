"""Constants for ServiceNow 2026 integration."""

from pathlib import Path

SERVICENOW_TABLE_COMPANY = "core_company"
SERVICENOW_TABLE_LOCATION = "cmn_location"
SERVICENOW_TABLE_MODEL = "cmdb_model"
SERVICENOW_TABLE_DEVICE = "cmdb_ci_netgear"

SERVICENOW_METADATA_SYS_ID = "ServiceNow Sys ID"
SERVICENOW_METADATA_URL = "ServiceNow URL"

SERVICENOW_METADATA_TYPES = (
    SERVICENOW_METADATA_SYS_ID,
    SERVICENOW_METADATA_URL,
)

# Duplicate-name normalization rules used by ServiceNowAdapter during ingest.
#
# Why this exists:
# ServiceNow data can contain display names that are not globally unique in practice.
# For example, two device models can share the same model name under different
# manufacturers, and two locations can share the same name under different parents.
#
# The adapter needs deterministic names to avoid collisions and ambiguous records when
# building DiffSync models. To solve that, the adapter groups records by model-specific
# "duplicate keys" and appends a short sys_id suffix only when duplicates are present.
#
# How each rule works:
# - name_field:
#   Which field should receive the suffix, e.g. "name" or "model".
# - key_fields:
#   Which fields define "same logical name scope" for collision detection.
#   If two records have identical values across these fields, they are considered
#   part of the same duplicate group.
# - allow_none_fields (optional):
#   Fields from key_fields that may be None and still participate in grouping.
#   Without this, a None in a key field causes the record to be skipped from
#   duplicate grouping for safety.
#
# Example:
# For locations, key_fields are ("parent_sys_id", "name"). If two locations are
# named "HQ" under the same parent, both names become "HQ (<first8-sys_id>)".
# If only one "HQ" exists under that parent, no suffix is added.
SERVICENOW_DUPLICATE_NAME_RULES = {
    "company": {
        "name_field": "name",
        "key_fields": ("name",),
    },
    "manufacturer": {
        "name_field": "name",
        "key_fields": ("name",),
    },
    "platform": {
        "name_field": "name",
        "key_fields": ("name",),
    },
    "device_type": {
        "name_field": "model",
        "key_fields": ("manufacturer_sys_id", "model"),
        "allow_none_fields": ("manufacturer_sys_id",),
    },
    "location": {
        "name_field": "name",
        "key_fields": ("parent_sys_id", "name"),
        "allow_none_fields": ("parent_sys_id",),
    },
    "device": {
        "name_field": "name",
        "key_fields": ("name",),
    },
}

DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent / "mapping.yaml"
DEFAULT_CLIENT_TIMEOUT = 30
DEFAULT_CLIENT_PAGE_SIZE = 1000
