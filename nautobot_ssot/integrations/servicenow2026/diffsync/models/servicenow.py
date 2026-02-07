"""DiffSync models representing ServiceNow records."""

from decimal import Decimal
from typing import Optional

from diffsync import DiffSyncModel

from nautobot_ssot.integrations.servicenow2026.diffsync.models import shared


class ServiceNowBase(DiffSyncModel):
    """Base DiffSync model for ServiceNow objects."""

    sys_id: str
    servicenow_sys_id: str
    name: Optional[str] = None
    servicenow_url: Optional[str] = None
    servicenow_table: Optional[str] = None
    servicenow_instance: Optional[str] = None


class ServiceNowCompany(ServiceNowBase):
    """ServiceNow company record."""

    _modelname = "company"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )


class ServiceNowManufacturer(ServiceNowBase):
    """ServiceNow manufacturer record."""

    _modelname = "manufacturer"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )


class ServiceNowPlatform(ServiceNowBase):
    """ServiceNow platform record."""

    _modelname = "platform"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )

    manufacturer_sys_id: Optional[str] = None


class ServiceNowDeviceType(ServiceNowBase):
    """ServiceNow device type record."""

    _modelname = "device_type"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.DEVICE_TYPE_ATTRIBUTES

    model: Optional[str] = None
    part_number: Optional[str] = None
    manufacturer_sys_id: Optional[str] = None


class ServiceNowLocation(ServiceNowBase):
    """ServiceNow location record."""

    _modelname = "location"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.LOCATION_ATTRIBUTES

    parent_sys_id: Optional[str] = None
    location_type__name: Optional[str] = None
    tenant_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    full_name: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None


class ServiceNowDevice(ServiceNowBase):
    """ServiceNow device record."""

    _modelname = "device"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.DEVICE_ATTRIBUTES

    location_sys_id: Optional[str] = None
    device_type_sys_id: Optional[str] = None
    platform_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    role__name: Optional[str] = None
    serial: Optional[str] = None
