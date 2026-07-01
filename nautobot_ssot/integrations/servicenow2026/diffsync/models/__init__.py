"""ServiceNow 2026 DiffSync models."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Annotated, Optional
from uuid import UUID

from diffsync import DiffSyncModel
from diffsync.exceptions import ObjectCrudException
from diffsync.exceptions import ObjectNotCreated, ObjectNotDeleted, ObjectNotUpdated
from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import (
    Device as NautobotDevice,
)
from nautobot.dcim.models import (
    DeviceType as NautobotDeviceType,
)
from nautobot.dcim.models import (
    Location as NautobotLocation,
)
from nautobot.dcim.models import (
    LocationType,
)
from nautobot.dcim.models import (
    Manufacturer as NautobotManufacturer,
)
from nautobot.dcim.models import (
    Platform as NautobotPlatform,
)
from nautobot.extras.models import Role, Status
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata
from nautobot.tenancy.models import Tenant as NautobotTenant

from nautobot_ssot.contrib import NautobotModel
from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.object_metadata import ObjectMetadataAnnotation, ObjectMetadataMixin
from nautobot_ssot.integrations.servicenow2026.utils import metadata as metadata_utils

METADATA_ATTRIBUTES = ("servicenow_url",)

COMPANY_ATTRIBUTES = ("name",) + METADATA_ATTRIBUTES

MANUFACTURER_ATTRIBUTES = ("name",) + METADATA_ATTRIBUTES

PLATFORM_ATTRIBUTES = ("name", "manufacturer_sys_id") + METADATA_ATTRIBUTES

DEVICE_TYPE_ATTRIBUTES = ("model", "part_number", "manufacturer_sys_id") + METADATA_ATTRIBUTES

LOCATION_ATTRIBUTES = (
    "name",
    "parent_sys_id",
    "location_type__name",
    "tenant_sys_id",
    "status__name",
    "latitude",
    "longitude",
) + METADATA_ATTRIBUTES

DEVICE_ATTRIBUTES = (
    "name",
    "location_sys_id",
    "device_type_sys_id",
    "platform_sys_id",
    "status__name",
    "role__name",
    "serial",
) + METADATA_ATTRIBUTES

UNASSIGNED_LOCATION_NAME = "SSoT Unassigned"
UNASSIGNED_DEVICE_TYPE_MODEL = "SSoT Unknown"
UNASSIGNED_MANUFACTURER_NAME = "SSoT Unknown"


class ServiceNowBaseModel(ObjectMetadataMixin, NautobotModel):
    """Base DiffSync model shared between ServiceNow and Nautobot."""

    sys_id: Optional[str] = None
    servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_SYS_ID)]
    servicenow_url: Annotated[Optional[str], ObjectMetadataAnnotation(key=constants.SERVICENOW_METADATA_URL)] = None

    @staticmethod
    def _is_servicenow_adapter(adapter) -> bool:
        """Return True when operating against the ServiceNow target adapter."""
        return hasattr(adapter, "client") and hasattr(adapter, "mapping")

    @classmethod
    def _get_mapping_entry(cls, adapter) -> dict[str, Any]:
        """Return mapping entry for this model type from the ServiceNow adapter."""
        mapping = getattr(adapter, "mapping", {}) or {}
        entry = mapping.get(cls.get_type())
        if not entry or "table" not in entry:
            raise ObjectCrudException(f"Mapping for model '{cls.get_type()}' is not defined.")
        return entry

    @classmethod
    def _to_payload_value(cls, value: Any) -> Any:
        """Convert values into JSON-safe payload values for ServiceNow."""
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {key: cls._to_payload_value(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._to_payload_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _map_to_servicenow_payload(cls, data: dict[str, Any], mapping_entry: dict[str, Any]) -> dict[str, Any]:
        """Map DiffSync field data to a ServiceNow table payload using mapping.yaml."""
        payload: dict[str, Any] = {}
        for mapping in mapping_entry.get("mappings", []):
            field = mapping.get("field")
            if not field or field not in data:
                continue
            value = cls._to_payload_value(data[field])
            if "column" in mapping:
                payload[mapping["column"]] = value
                continue
            if "reference" in mapping:
                key = mapping["reference"].get("key")
                if key:
                    payload[key] = value
        return payload

    @classmethod
    def _extract_nautobot_pk_from_identifier(cls, identifiers: dict[str, Any]) -> Optional[UUID]:
        """Extract Nautobot object PK from synthetic outbound identifier, if present."""
        value = identifiers.get("servicenow_sys_id")
        if not isinstance(value, str) or not value.startswith("nautobot-"):
            return None
        try:
            return UUID(value.replace("nautobot-", "", 1))
        except ValueError:
            return None

    @classmethod
    def _upsert_metadata_value(cls, obj, metadata_name: str, value: str) -> None:
        """Create or update one ObjectMetadata value on a Nautobot object."""
        metadata_type = MetadataType.objects.filter(name=metadata_name).first()
        if not metadata_type:
            return
        content_type = ContentType.objects.get_for_model(type(obj))
        if content_type not in metadata_type.content_types.all():
            metadata_type.content_types.add(content_type)
        metadata, created = ObjectMetadata.objects.get_or_create(
            assigned_object_id=obj.id,
            assigned_object_type=content_type,
            metadata_type=metadata_type,
            defaults={"value": value, "scoped_fields": []},
        )
        if not created and metadata.value != value:
            metadata.value = value
            metadata.validated_save()

    @classmethod
    def _persist_created_servicenow_metadata(
        cls,
        adapter,
        identifiers: dict[str, Any],
        table: str,
        created_sys_id: str,
    ) -> Optional[str]:
        """Persist created ServiceNow identifiers back into Nautobot ObjectMetadata."""
        nautobot_pk = cls._extract_nautobot_pk_from_identifier(identifiers)
        if nautobot_pk is None:
            return None
        obj = cls._model.objects.filter(pk=nautobot_pk).first()
        if not obj:
            return None
        cls._upsert_metadata_value(obj, constants.SERVICENOW_METADATA_SYS_ID, created_sys_id)
        created_url = metadata_utils.build_servicenow_url(
            instance=getattr(adapter.client.integration, "remote_url", None),
            table=table,
            sys_id=created_sys_id,
        )
        if created_url:
            cls._upsert_metadata_value(obj, constants.SERVICENOW_METADATA_URL, created_url)
        return created_url

    @classmethod
    def create(cls, adapter, ids, attrs):
        """Create object in ServiceNow when running Nautobot->ServiceNow sync."""
        if not cls._is_servicenow_adapter(adapter):
            return super().create(adapter, ids, attrs)
        try:
            mapping_entry = cls._get_mapping_entry(adapter)
            payload = cls._map_to_servicenow_payload({**ids, **attrs}, mapping_entry)
            created_record = adapter.client.create_record(mapping_entry["table"], payload)
            created_sys_id = created_record.get("sys_id")
            if not created_sys_id:
                raise ObjectCrudException(f"ServiceNow create response did not include sys_id: {created_record}")
            created_url = cls._persist_created_servicenow_metadata(
                adapter=adapter,
                identifiers=ids,
                table=mapping_entry["table"],
                created_sys_id=created_sys_id,
            )
        except Exception as error:  # pylint: disable=broad-except
            raise ObjectNotCreated(
                f"Failed to create ServiceNow {cls.get_type()} with identifiers {ids} and attributes {attrs}: {error}"
            ) from error
        model = DiffSyncModel.create.__func__(cls, adapter, ids, attrs)
        model.servicenow_sys_id = created_sys_id
        if created_url:
            model.servicenow_url = created_url
        return model

    def update(self, attrs):
        """Update object in ServiceNow when running Nautobot->ServiceNow sync."""
        if not self._is_servicenow_adapter(self.adapter):
            return super().update(attrs)
        if not self.servicenow_sys_id:
            raise ObjectNotUpdated(
                f"Cannot update ServiceNow {self.get_type()} without a servicenow_sys_id identifier."
            )
        try:
            mapping_entry = self._get_mapping_entry(self.adapter)
            payload = self._map_to_servicenow_payload(attrs, mapping_entry)
            if payload:
                self.adapter.client.update_record(mapping_entry["table"], self.servicenow_sys_id, payload)
        except Exception as error:  # pylint: disable=broad-except
            raise ObjectNotUpdated(
                f"Failed to update ServiceNow {self.get_type()} {self.servicenow_sys_id} with attrs {attrs}: {error}"
            ) from error
        return DiffSyncModel.update(self, attrs)

    def delete(self):
        """Delete object in ServiceNow when running Nautobot->ServiceNow sync."""
        if not self._is_servicenow_adapter(self.adapter):
            return super().delete()
        if not self.servicenow_sys_id:
            raise ObjectNotDeleted(
                f"Cannot delete ServiceNow {self.get_type()} without a servicenow_sys_id identifier."
            )
        try:
            mapping_entry = self._get_mapping_entry(self.adapter)
            self.adapter.client.delete_record(mapping_entry["table"], self.servicenow_sys_id)
        except Exception as error:  # pylint: disable=broad-except
            raise ObjectNotDeleted(
                f"Failed to delete ServiceNow {self.get_type()} {self.servicenow_sys_id}: {error}"
            ) from error
        return DiffSyncModel.delete(self)


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
    device_content_type = ContentType.objects.get_for_model(NautobotDevice)
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
    location_content_type = ContentType.objects.get_for_model(NautobotLocation)
    if location_content_type not in status.content_types.all():
        status.content_types.add(location_content_type)
        status.validated_save()
    return status


def _get_unassigned_location(adapter) -> Optional[NautobotLocation]:
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


def _ensure_unassigned_location(location_type: LocationType, status: Status) -> NautobotLocation:
    """Ensure the fallback location exists with the desired attributes.

    Args:
        location_type: LocationType to apply.
        status: Status to apply.

    Returns:
        Location instance.
    """
    location, created = NautobotLocation.objects.get_or_create(
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


def _get_unassigned_device_type() -> Optional[NautobotDeviceType]:
    """Return (and create if needed) the fallback DeviceType for missing ServiceNow device types."""
    manufacturer = _get_unassigned_manufacturer()
    if not manufacturer:
        return None
    device_type, created = NautobotDeviceType.objects.get_or_create(
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


def _get_unassigned_manufacturer() -> Optional[NautobotManufacturer]:
    """Return (and create if needed) the fallback Manufacturer for missing ServiceNow data."""
    manufacturer, _ = NautobotManufacturer.objects.get_or_create(name=UNASSIGNED_MANUFACTURER_NAME)
    return manufacturer


@dataclass(frozen=True)
class DeviceForeignKeyContext:
    """Container for resolved device foreign key context."""

    location_sys_id: Optional[str]
    device_type_sys_id: Optional[str]
    location: Optional[NautobotLocation]
    device_type: Optional[NautobotDeviceType]


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

    location = _resolve_metadata_foreign_key(NautobotLocation, location_sys_id, adapter, "location")
    if location:
        parameters["location"] = location
    device_type = _resolve_metadata_foreign_key(NautobotDeviceType, device_type_sys_id, adapter, "device type")
    if device_type:
        parameters["device_type"] = device_type
    platform = _resolve_metadata_foreign_key(NautobotPlatform, platform_sys_id, adapter, "platform")
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


class Company(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow companies and Nautobot tenants."""

    _model = NautobotTenant
    _modelname = "company"
    _identifiers = ("servicenow_sys_id",)
    _attributes = COMPANY_ATTRIBUTES

    name: str


class Manufacturer(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow manufacturers and Nautobot manufacturers."""

    _model = NautobotManufacturer
    _modelname = "manufacturer"
    _identifiers = ("servicenow_sys_id",)
    _attributes = MANUFACTURER_ATTRIBUTES

    name: str


class Platform(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow platforms and Nautobot platforms."""

    _model = NautobotPlatform
    _modelname = "platform"
    _identifiers = ("servicenow_sys_id",)
    _attributes = PLATFORM_ATTRIBUTES

    name: str
    manufacturer: Optional[NautobotManufacturer] = None
    manufacturer_sys_id: Optional[str] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot Platform object and resolve manufacturer by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        manufacturer_sys_id = parameters.pop("manufacturer_sys_id", None)
        manufacturer = _resolve_metadata_foreign_key(NautobotManufacturer, manufacturer_sys_id, adapter, "manufacturer")
        if manufacturer:
            parameters["manufacturer"] = manufacturer
        super()._update_obj_with_parameters(obj, parameters, adapter)


class DeviceType(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow device types and Nautobot device types."""

    _model = NautobotDeviceType
    _modelname = "device_type"
    _identifiers = ("servicenow_sys_id",)
    _attributes = DEVICE_TYPE_ATTRIBUTES

    model: str
    manufacturer: Optional[NautobotManufacturer] = None
    part_number: Optional[str] = None
    manufacturer_sys_id: Optional[str] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot DeviceType object and resolve manufacturer by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        manufacturer_sys_id = parameters.pop("manufacturer_sys_id", None)
        manufacturer = _resolve_metadata_foreign_key(NautobotManufacturer, manufacturer_sys_id, adapter, "manufacturer")
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


class Location(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow locations and Nautobot locations."""

    _model = NautobotLocation
    _modelname = "location"
    _identifiers = ("servicenow_sys_id",)
    _attributes = LOCATION_ATTRIBUTES

    name: str
    parent: Optional[NautobotLocation] = None
    parent_sys_id: Optional[str] = None
    location_type__name: Optional[str] = None
    tenant: Optional[NautobotTenant] = None
    tenant_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot Location object and resolve parent by metadata.

        Args:
            obj: Nautobot ORM object to update.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        parent_sys_id = parameters.pop("parent_sys_id", None)
        parent = _resolve_metadata_foreign_key(NautobotLocation, parent_sys_id, adapter, "parent location")
        if parent:
            parameters["parent"] = parent
        tenant_sys_id = parameters.pop("tenant_sys_id", None)
        tenant = _resolve_metadata_foreign_key(NautobotTenant, tenant_sys_id, adapter, "tenant")
        if tenant:
            parameters["tenant"] = tenant
        if obj.pk is None and not parameters.get("location_type__name"):
            raise ObjectCrudException(f"Location '{parameters.get('name')}' missing location_type.")
        super()._update_obj_with_parameters(obj, parameters, adapter)


class Device(ServiceNowBaseModel):
    """Shared DiffSync model for ServiceNow devices and Nautobot devices."""

    _model = NautobotDevice
    _modelname = "device"
    _identifiers = ("servicenow_sys_id",)
    _attributes = DEVICE_ATTRIBUTES

    name: str
    location: Optional[NautobotLocation] = None
    location_sys_id: Optional[str] = None
    device_type: Optional[NautobotDeviceType] = None
    device_type_sys_id: Optional[str] = None
    platform: Optional[NautobotPlatform] = None
    platform_sys_id: Optional[str] = None
    status__name: Optional[str] = None
    role__name: Optional[str] = None
    serial: Optional[str] = None

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
