"""Base DiffSync models for ServiceNow 2026 integration."""

from diffsync.exceptions import ObjectCrudException
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata
from typing_extensions import get_type_hints

from nautobot_ssot.contrib.model import NautobotModel
from nautobot_ssot.integrations.servicenow2026.annotations import ObjectMetadataAnnotation


class ServiceNowNautobotModel(NautobotModel):
    """Nautobot DiffSync base model with ObjectMetadata support for ServiceNow 2026."""

    @classmethod
    def _handle_single_field(cls, field, obj, value, relationship_fields, adapter):  # pylint: disable=too-many-arguments,too-many-locals
        """Handle a single field update, including ObjectMetadata annotations.

        Args:
            field: Field name to set.
            obj: Nautobot ORM object being updated.
            value: Value to set.
            relationship_fields: Relationship tracking dictionary.
            adapter: DiffSync adapter for lookups.
        """
        type_hints = get_type_hints(cls, include_extras=True)
        if field not in type_hints:
            super()._handle_single_field(field, obj, value, relationship_fields, adapter)
            return
        metadata_for_this_field = getattr(type_hints[field], "__metadata__", [])
        for metadata in metadata_for_this_field:
            if isinstance(metadata, ObjectMetadataAnnotation):
                if value is None:
                    return
                relationship_fields["object_metadata_fields"].append(
                    {
                        "annotation": metadata,
                        "value": value,
                    }
                )
                return
        super()._handle_single_field(field, obj, value, relationship_fields, adapter)

    @classmethod
    def _build_relationship_fields(cls):
        """Return initial relationship tracking structures for ServiceNow models.

        Returns:
            Dictionary of relationship field containers.
        """
        fields = super()._build_relationship_fields()
        fields["object_metadata_fields"] = []
        return fields

    @classmethod
    def _finalize_obj_update(cls, obj, relationship_fields, adapter):
        """Finalize object metadata updates before relationships.

        Args:
            obj: Nautobot ORM object being updated.
            relationship_fields: Relationship tracking dictionary.
            adapter: DiffSync adapter for lookups.
        """
        cls._set_object_metadata_fields(relationship_fields.get("object_metadata_fields", []), obj)
        super()._finalize_obj_update(obj, relationship_fields, adapter)

    @classmethod
    def _handle_validated_save_error(cls, error, parameters):
        """Raise a CRUD error with JSON-safe parameters.

        Args:
            error: Validation exception raised by validated_save.
            parameters: Parameters applied to the object.
        """
        safe_parameters = cls._serialize_parameters(parameters)
        raise ObjectCrudException(
            f"Validated save failed for Django object:\n{error}\nParameters: {safe_parameters}"
        ) from error

    @classmethod
    def _serialize_parameters(cls, parameters):
        """Return a JSON-safe version of parameters for logging."""

        def _serialize_value(value):
            if isinstance(value, Model):
                return str(value)
            if isinstance(value, dict):
                return {key: _serialize_value(val) for key, val in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [_serialize_value(item) for item in value]
            return value

        return {key: _serialize_value(val) for key, val in parameters.items()}

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
