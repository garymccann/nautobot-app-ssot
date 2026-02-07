"""Nautobot DiffSync adapter for ServiceNow 2026."""

from typing import Optional

from django.contrib.contenttypes.models import ContentType
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata
from typing_extensions import get_type_hints

from nautobot_ssot.contrib.adapter import NautobotAdapter
from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.annotations import ObjectMetadataAnnotation
from nautobot_ssot.integrations.servicenow2026.diffsync.models import nautobot as models
from nautobot_ssot.integrations.servicenow2026.utils import metadata as metadata_utils


class TheNautobotAdapter(NautobotAdapter):
    """DiffSync adapter loading data from Nautobot for ServiceNow 2026."""

    tenant = models.NautobotTenant
    manufacturer = models.NautobotManufacturer
    platform = models.NautobotPlatform
    device_type = models.NautobotDeviceType
    location = models.NautobotLocation
    device = models.NautobotDevice

    top_level = (
        "tenant",
        "manufacturer",
        "platform",
        "device_type",
        "location",
        "device",
    )

    def __init__(self, *args, include_without_sys_id: bool = False, **kwargs):
        """Initialize the Nautobot adapter.

        Args:
            include_without_sys_id: Whether to include objects without ServiceNow sys_id metadata.
        """
        super().__init__(*args, **kwargs)
        self.include_without_sys_id = include_without_sys_id
        self._sys_id_metadata_type: Optional[MetadataType] = None

    def _load_objects(self, diffsync_model):
        """Load objects with ServiceNow metadata into DiffSync models.

        Args:
            diffsync_model: DiffSync model class to load.
        """
        parameter_names = diffsync_model.get_synced_attributes()
        for database_object in diffsync_model._get_queryset():  # pylint: disable=protected-access
            if not self.include_without_sys_id and not self._has_sys_id_metadata(database_object):
                continue
            self._load_single_object(database_object, diffsync_model, parameter_names)

    def _handle_single_parameter(self, parameters, parameter_name, database_object, diffsync_model):
        """Handle a single parameter load, including ObjectMetadata annotations.

        Args:
            parameters: Mutable parameters dictionary.
            parameter_name: Parameter name to load.
            database_object: Nautobot ORM object being loaded.
            diffsync_model: DiffSync model class.
        """
        related_sys_id_fields = {
            "manufacturer_sys_id": "manufacturer",
            "tenant_sys_id": "tenant",
            "parent_sys_id": "parent",
            "location_sys_id": "location",
            "device_type_sys_id": "device_type",
            "platform_sys_id": "platform",
        }
        if parameter_name in related_sys_id_fields:
            related_obj = getattr(database_object, related_sys_id_fields[parameter_name], None)
            if related_obj is None:
                parameters[parameter_name] = None
                return
            parameters[parameter_name] = metadata_utils.get_object_metadata_value(
                related_obj, constants.SERVICENOW_METADATA_SYS_ID
            )
            return
        type_hints = get_type_hints(diffsync_model, include_extras=True)
        metadata_for_this_field = getattr(type_hints[parameter_name], "__metadata__", [])
        for metadata in metadata_for_this_field:
            if isinstance(metadata, ObjectMetadataAnnotation):
                parameters[parameter_name] = self._load_object_metadata_value(database_object, metadata)
                return
        super()._handle_single_parameter(parameters, parameter_name, database_object, diffsync_model)

    def _has_sys_id_metadata(self, database_object) -> bool:
        """Return True if object has a ServiceNow sys_id metadata value.

        Args:
            database_object: Nautobot ORM object to inspect.

        Returns:
            True if metadata exists, otherwise False.
        """
        metadata_type = self._get_sys_id_metadata_type()
        if not metadata_type:
            return False
        content_type = ContentType.objects.get_for_model(type(database_object))
        return ObjectMetadata.objects.filter(
            assigned_object_id=database_object.id,
            assigned_object_type=content_type,
            metadata_type=metadata_type,
        ).exists()

    def _get_sys_id_metadata_type(self) -> Optional[MetadataType]:
        """Return the ServiceNow sys_id MetadataType, caching if needed.

        Returns:
            MetadataType if found, otherwise None.
        """
        if self._sys_id_metadata_type is None:
            self._sys_id_metadata_type = MetadataType.objects.filter(name=constants.SERVICENOW_METADATA_SYS_ID).first()
        return self._sys_id_metadata_type

    @staticmethod
    def _load_object_metadata_value(database_object, annotation: ObjectMetadataAnnotation):
        """Load ObjectMetadata value for the given annotation.

        Args:
            database_object: Nautobot ORM object to read metadata from.
            annotation: ObjectMetadataAnnotation instance.

        Returns:
            Metadata value if found, otherwise None.
        """
        if not annotation.key:
            return None
        content_type = ContentType.objects.get_for_model(type(database_object))
        metadata = ObjectMetadata.objects.filter(
            assigned_object_id=database_object.id,
            assigned_object_type=content_type,
            metadata_type__name=annotation.key,
        ).first()
        if not metadata:
            return None
        return metadata.value
