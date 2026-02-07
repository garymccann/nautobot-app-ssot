"""DiffSync models representing Nautobot objects for ServiceNow 2026."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Optional

from diffsync.exceptions import ObjectCrudException
from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import Device, DeviceType, Location, LocationType, Manufacturer, Platform
from nautobot.extras.models import Role, Status
from nautobot.tenancy.models import Tenant

from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.annotations import ObjectMetadataAnnotation
from nautobot_ssot.integrations.servicenow2026.diffsync.models import shared
from nautobot_ssot.integrations.servicenow2026.diffsync.models.base import ServiceNowNautobotModel
from nautobot_ssot.integrations.servicenow2026.utils import metadata as metadata_utils

UNASSIGNED_LOCATION_NAME = "SSoT Unassigned"
UNASSIGNED_DEVICE_TYPE_MODEL = "SSoT Unknown"
UNASSIGNED_MANUFACTURER_NAME = "SSoT Unknown"


def _resolve_metadata_foreign_key(model, sys_id: Optional[str], adapter, label: str):
    """Resolve a foreign key using ServiceNow sys_id metadata.

    Args:
        model: Django model class to resolve.
        sys_id: ServiceNow sys_id value.
        adapter: DiffSync adapter for logging.
        label: Friendly label for logging.

    Returns:
        ORM object if found, otherwise None.
    """
    if not sys_id:
        return None
    obj = metadata_utils.get_object_by_sys_id(model, sys_id)
    if not obj:
        adapter.job.logger.debug("Unable to resolve %s with sys_id %s.", label, sys_id)
    return obj


def _resolve_device_role(role_name: Optional[str]) -> Optional[Role]:
    """Resolve a device Role by name and ensure it is scoped to Devices."""
    if not role_name:
        return None
    device_content_type = ContentType.objects.get_for_model(Device)
    role = Role.objects.filter(name=role_name, content_types=device_content_type).first()
    if role:
        return role
    role = Role.objects.filter(name=role_name).first()
    if role and device_content_type not in role.content_types.all():
        role.content_types.add(device_content_type)
        role.validated_save()
    return role


def _get_mapping_default(adapter, model_name: str, field: str) -> Optional[str]:
    """Return a default value from mapping defaults if present."""
    defaults = getattr(adapter.job, "mapping_defaults", {}) or {}
    return defaults.get(model_name, {}).get(field)


def _resolve_location_status(status_name: Optional[str]) -> Optional[Status]:
    """Resolve a location Status by name and ensure it is scoped to Locations."""
    if not status_name:
        return None
    status = Status.objects.filter(name=status_name).first()
    if not status:
        return None
    location_content_type = ContentType.objects.get_for_model(Location)
    if location_content_type not in status.content_types.all():
        status.content_types.add(location_content_type)
        status.validated_save()
    return status


def _get_unassigned_location(adapter) -> Optional[Location]:
    """Return (and create if needed) the fallback Location for missing ServiceNow locations."""
    resolved_location_type = _resolve_fallback_location_type(adapter)
    if not resolved_location_type:
        adapter.job.logger.warning("Unable to create fallback location; no root LocationType exists.")
        return None
    resolved_status = _resolve_fallback_location_status(adapter)
    if not resolved_status:
        adapter.job.logger.warning("Unable to create fallback location; no Location Status found.")
        return None
    return _ensure_unassigned_location(resolved_location_type, resolved_status)


def _resolve_fallback_location_type(adapter) -> Optional[LocationType]:
    """Resolve the LocationType for fallback locations.

    Args:
        adapter: DiffSync adapter for logging and defaults.

    Returns:
        LocationType instance or None.
    """
    location_type = _get_mapping_default(adapter, "location", "location_type__name")
    if isinstance(location_type, LocationType) and location_type.parent_id is None:
        return location_type
    if isinstance(location_type, str):
        resolved = LocationType.objects.filter(name=location_type, parent__isnull=True).first()
        if resolved:
            return resolved
    return LocationType.objects.filter(parent__isnull=True).order_by("name").first()


def _resolve_fallback_location_status(adapter) -> Optional[Status]:
    """Resolve the Status for fallback locations.

    Args:
        adapter: DiffSync adapter for logging and defaults.

    Returns:
        Status instance or None.
    """
    status_value = _get_mapping_default(adapter, "location", "status__name")
    if isinstance(status_value, Status):
        return status_value
    if isinstance(status_value, str):
        resolved = _resolve_location_status(status_value)
        if resolved:
            return resolved
    return _resolve_location_status("Active")


def _ensure_unassigned_location(location_type: LocationType, status: Status) -> Location:
    """Ensure the fallback location exists with the desired attributes.

    Args:
        location_type: LocationType to apply.
        status: Status to apply.

    Returns:
        Location instance.
    """
    location, created = Location.objects.get_or_create(
        name=UNASSIGNED_LOCATION_NAME,
        defaults={"location_type": location_type, "status": status},
    )
    if created:
        return location
    needs_save = False
    if location.location_type_id != location_type.id:
        location.location_type = location_type
        needs_save = True
    if location.status_id != status.id:
        location.status = status
        needs_save = True
    if needs_save:
        location.validated_save()
    return location


def _get_unassigned_device_type() -> Optional[DeviceType]:
    """Return (and create if needed) the fallback DeviceType for missing ServiceNow device types."""
    manufacturer = _get_unassigned_manufacturer()
    if not manufacturer:
        return None
    device_type, created = DeviceType.objects.get_or_create(
        manufacturer=manufacturer,
        model=UNASSIGNED_DEVICE_TYPE_MODEL,
    )
    if not created:
        needs_save = False
        if device_type.manufacturer_id != manufacturer.id:
            device_type.manufacturer = manufacturer
            needs_save = True
        if device_type.model != UNASSIGNED_DEVICE_TYPE_MODEL:
            device_type.model = UNASSIGNED_DEVICE_TYPE_MODEL
            needs_save = True
        if needs_save:
            device_type.validated_save()
    return device_type


def _get_unassigned_manufacturer() -> Optional[Manufacturer]:
    """Return (and create if needed) the fallback Manufacturer for missing ServiceNow data."""
    manufacturer, _ = Manufacturer.objects.get_or_create(name=UNASSIGNED_MANUFACTURER_NAME)
    return manufacturer


@dataclass(frozen=True)
class DeviceForeignKeyContext:
    """Container for resolved device foreign key context."""

    location_sys_id: Optional[str]
    device_type_sys_id: Optional[str]
    location: Optional[Location]
    device_type: Optional[DeviceType]


def _resolve_device_foreign_keys(parameters, adapter) -> DeviceForeignKeyContext:
    """Resolve device foreign keys by ServiceNow metadata.

    Args:
        parameters: Parameter dictionary to update.
        adapter: DiffSync adapter for logging.

    Returns:
        DeviceForeignKeyContext with resolved relationships.
    """
    location_sys_id = parameters.pop("location_sys_id", None)
    device_type_sys_id = parameters.pop("device_type_sys_id", None)
    platform_sys_id = parameters.pop("platform_sys_id", None)

    location = _resolve_metadata_foreign_key(Location, location_sys_id, adapter, "location")
    if location:
        parameters["location"] = location
    device_type = _resolve_metadata_foreign_key(DeviceType, device_type_sys_id, adapter, "device type")
    if device_type:
        parameters["device_type"] = device_type
    platform = _resolve_metadata_foreign_key(Platform, platform_sys_id, adapter, "platform")
    if platform:
        parameters["platform"] = platform
    return DeviceForeignKeyContext(
        location_sys_id=location_sys_id,
        device_type_sys_id=device_type_sys_id,
        location=location,
        device_type=device_type,
    )


def _ensure_device_requirements(obj, parameters, adapter, context: DeviceForeignKeyContext):
    """Ensure required device relationships are present, using fallbacks when needed.

    Args:
        obj: Nautobot Device instance being updated.
        parameters: Parameter dictionary to update.
        adapter: DiffSync adapter for logging.
        context: Resolved foreign key context.
    """
    if not context.location and obj.location_id is None:
        fallback_location = _get_unassigned_location(adapter)
        if fallback_location:
            parameters["location"] = fallback_location
        else:
            raise ObjectCrudException(
                f"Device '{parameters.get('name')}' missing location for sys_id {context.location_sys_id}."
            )
    if not context.device_type and obj.device_type_id is None:
        fallback_device_type = _get_unassigned_device_type()
        if fallback_device_type:
            parameters["device_type"] = fallback_device_type
        else:
            raise ObjectCrudException(
                f"Device '{parameters.get('name')}' missing device type for sys_id {context.device_type_sys_id}."
            )


def _ensure_device_role(parameters, adapter, obj):
    """Ensure device role is set or resolvable.

    Args:
        parameters: Parameter dictionary to update.
        adapter: DiffSync adapter for logging.
        obj: Nautobot Device instance being updated.
    """
    role_name = parameters.get("role__name")
    role = _resolve_device_role(role_name)
    if not role:
        default_role = _get_mapping_default(adapter, "device", "role__name")
        if isinstance(default_role, Role):
            parameters["role__name"] = default_role.name
            role = _resolve_device_role(default_role.name)
        elif default_role:
            parameters["role__name"] = default_role
            role = _resolve_device_role(default_role)
        else:
            parameters.pop("role__name", None)
    if not role and obj.role_id is None:
        raise ObjectCrudException(
            f"Device '{parameters.get('name')}' missing role; set a default Device Role or map role__name."
        )


class NautobotTenant(ServiceNowNautobotModel):
    """Nautobot Tenant model for ServiceNow company records."""

    _model = Tenant
    _modelname = "tenant"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )

    name: str
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None


class NautobotManufacturer(ServiceNowNautobotModel):
    """Nautobot Manufacturer model for ServiceNow manufacturer records."""

    _model = Manufacturer
    _modelname = "manufacturer"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )

    name: str
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None


class NautobotPlatform(ServiceNowNautobotModel):
    """Nautobot Platform model for ServiceNow platform records."""

    _model = Platform
    _modelname = "platform"
    _identifiers = ("servicenow_sys_id",)
    _attributes = (
        "name",
        "manufacturer_sys_id",
        "servicenow_url",
        "servicenow_table",
        "servicenow_instance",
    )

    name: str
    manufacturer: Optional[Manufacturer] = None
    manufacturer_sys_id: Optional[str] = None
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot Platform object and resolve manufacturer by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        manufacturer_sys_id = parameters.pop("manufacturer_sys_id", None)
        manufacturer = _resolve_metadata_foreign_key(Manufacturer, manufacturer_sys_id, adapter, "manufacturer")
        if manufacturer:
            parameters["manufacturer"] = manufacturer
        super()._update_obj_with_parameters(obj, parameters, adapter)


class NautobotDeviceType(ServiceNowNautobotModel):
    """Nautobot DeviceType model for ServiceNow device type records."""

    _model = DeviceType
    _modelname = "device_type"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.DEVICE_TYPE_ATTRIBUTES

    model: str
    manufacturer: Optional[Manufacturer] = None
    part_number: Optional[str] = None
    manufacturer_sys_id: Optional[str] = None
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot DeviceType object and resolve manufacturer by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        manufacturer_sys_id = parameters.pop("manufacturer_sys_id", None)
        manufacturer = _resolve_metadata_foreign_key(Manufacturer, manufacturer_sys_id, adapter, "manufacturer")
        if manufacturer:
            parameters["manufacturer"] = manufacturer
        elif obj.manufacturer_id is None:
            fallback_manufacturer = _get_unassigned_manufacturer()
            if fallback_manufacturer:
                parameters["manufacturer"] = fallback_manufacturer
            else:
                raise ObjectCrudException(
                    f"DeviceType '{parameters.get('model')}' missing manufacturer for sys_id {manufacturer_sys_id}."
                )
        super()._update_obj_with_parameters(obj, parameters, adapter)


class NautobotLocation(ServiceNowNautobotModel):
    """Nautobot Location model for ServiceNow location records."""

    _model = Location
    _modelname = "location"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.LOCATION_ATTRIBUTES

    name: str
    parent: Optional[Location] = None
    parent_sys_id: Optional[str] = None
    location_type__name: Optional[str] = None
    tenant: Optional[Tenant] = None
    tenant_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot Location object and resolve parent by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        parent_sys_id = parameters.pop("parent_sys_id", None)
        parent = _resolve_metadata_foreign_key(Location, parent_sys_id, adapter, "parent location")
        if parent:
            parameters["parent"] = parent
        tenant_sys_id = parameters.pop("tenant_sys_id", None)
        tenant = _resolve_metadata_foreign_key(Tenant, tenant_sys_id, adapter, "tenant")
        if tenant:
            parameters["tenant"] = tenant
        if obj.pk is None and not parameters.get("location_type__name"):
            raise ObjectCrudException(f"Location '{parameters.get('name')}' missing location_type.")
        super()._update_obj_with_parameters(obj, parameters, adapter)


class NautobotDevice(ServiceNowNautobotModel):
    """Nautobot Device model for ServiceNow device records."""

    _model = Device
    _modelname = "device"
    _identifiers = ("servicenow_sys_id",)
    _attributes = shared.DEVICE_ATTRIBUTES

    name: str
    location: Optional[Location] = None
    location_sys_id: Optional[str] = None
    device_type: Optional[DeviceType] = None
    device_type_sys_id: Optional[str] = None
    platform: Optional[Platform] = None
    platform_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    role__name: Optional[str] = None
    serial: Optional[str] = None
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None
    servicenow_table: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_TABLE)] = None
    servicenow_instance: Annotated[
        Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_INSTANCE)
    ] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot Device object and resolve foreign keys by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        context = _resolve_device_foreign_keys(parameters, adapter)
        _ensure_device_requirements(obj, parameters, adapter, context)
        _ensure_device_role(parameters, adapter, obj)
        super()._update_obj_with_parameters(obj, parameters, adapter)
