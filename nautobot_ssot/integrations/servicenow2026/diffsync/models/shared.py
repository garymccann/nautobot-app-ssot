"""Shared attribute constants for ServiceNow 2026 DiffSync models."""

LOCATION_ATTRIBUTES = (
    "name",
    "parent_sys_id",
    "location_type__name",
    "tenant_sys_id",
    "status__name",
    "latitude",
    "longitude",
    "servicenow_url",
    "servicenow_table",
    "servicenow_instance",
)

DEVICE_ATTRIBUTES = (
    "name",
    "location_sys_id",
    "device_type_sys_id",
    "platform_sys_id",
    "status__name",
    "role__name",
    "serial",
    "servicenow_url",
    "servicenow_table",
    "servicenow_instance",
)

DEVICE_TYPE_ATTRIBUTES = (
    "model",
    "part_number",
    "manufacturer_sys_id",
    "servicenow_url",
    "servicenow_table",
    "servicenow_instance",
)
