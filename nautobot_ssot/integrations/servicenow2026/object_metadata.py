"""ServiceNow 2026 ObjectMetadata helpers."""

from dataclasses import dataclass
from typing import Any, Optional

from diffsync.exceptions import ObjectCrudException
from django.contrib.contenttypes.models import ContentType
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata
from typing_extensions import get_type_hints

from nautobot_ssot.contrib.types import CustomAnnotation


@dataclass
class ObjectMetadataAnnotation(CustomAnnotation):
    """Map a model field to an ObjectMetadata type name.

    For usage with `typing.Annotated`.

    This allows DiffSync models to read and write ObjectMetadata values as part
    of the normal attribute sync process.

    Example:
        Given a metadata type named "ServiceNow Sys ID" on the Device model:

        ```python
        class DeviceModel(ServiceNowNautobotModel):
            _model: Device
            _identifiers = ("name",)
            _attributes = ("servicenow_sys_id",)

            servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key="ServiceNow Sys ID")]
        ```
    """

    # TODO: Delete on 3.0, keep around for backwards compatibility for now
    name: Optional[str] = None
    key: Optional[str] = None

    def __post_init__(self) -> None:
        """Compatibility layer with using 'name' instead of 'key'.

        If `self.key` isn't set, fall back to the old behaviour.
        """
        if not self.key:
            if self.name:
                self.key = self.name
            else:
                raise ValueError("The 'key' field on ObjectMetadataAnnotation needs to be set.")


class ObjectMetadataMixin:
    """Mixin adding ObjectMetadata handling to DiffSync Nautobot models.
    The aim of this mixin is to showcase the code to be added to contrib
    model class to support ObjectMetadata.
    """

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot ORM object with ObjectMetadata annotation support.

        Args:
            obj: Nautobot ORM object being updated.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        object_metadata_fields, model_parameters = cls._extract_object_metadata_fields(parameters)
        super()._update_obj_with_parameters(obj, model_parameters, adapter)
        cls._set_object_metadata_fields(object_metadata_fields, obj)

    @classmethod
    def _extract_object_metadata_fields(cls, parameters: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Split annotated ObjectMetadata fields from standard model parameters.

        Returns:
            Tuple of (object_metadata_fields, model_parameters).
        """
        type_hints = get_type_hints(cls, include_extras=True)
        object_metadata_fields: list[dict[str, Any]] = []
        model_parameters: dict[str, Any] = {}
        for field, value in parameters.items():
            super()._check_field(field)
            metadata_for_this_field = getattr(type_hints.get(field), "__metadata__", [])
            annotation = next(
                (metadata for metadata in metadata_for_this_field if isinstance(metadata, ObjectMetadataAnnotation)),
                None,
            )
            if not annotation:
                model_parameters[field] = value
                continue
            if value is not None:
                object_metadata_fields.append({"annotation": annotation, "value": value})
        return object_metadata_fields, model_parameters

    @classmethod
    def _set_object_metadata_fields(cls, object_metadata_fields, obj):
        """Update ObjectMetadata values for the given object.

        Args:
            object_metadata_fields: List of metadata entries to apply.
            obj: Nautobot ORM object being updated.
        """
        if not object_metadata_fields:
            return
        content_type = ContentType.objects.get_for_model(type(obj))
        for entry in object_metadata_fields:
            annotation = entry["annotation"]
            value = entry["value"]
            if not annotation.key:
                continue
            metadata_type = MetadataType.objects.filter(name=annotation.key).first()
            if not metadata_type:
                raise ObjectCrudException(f"MetadataType '{annotation.key}' is not defined.")
            if content_type not in metadata_type.content_types.all():
                metadata_type.content_types.add(content_type)
            metadata, created = ObjectMetadata.objects.get_or_create(
                assigned_object_id=obj.id,
                assigned_object_type=content_type,
                metadata_type=metadata_type,
                defaults={"value": value, "scoped_fields": []},
            )
            if not created:
                metadata.value = value
                metadata.validated_save()
