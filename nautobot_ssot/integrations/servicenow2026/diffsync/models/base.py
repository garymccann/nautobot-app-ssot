"""Base DiffSync models for ServiceNow 2026 integration."""

from collections import defaultdict

from diffsync.exceptions import ObjectCrudException
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
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
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Update a Nautobot ORM object with ObjectMetadata support.

        Args:
            obj: Nautobot ORM object being updated.
            parameters: Field parameters to apply.
            adapter: DiffSync adapter for lookups.
        """
        relationship_fields = cls._init_relationship_fields()
        cls._collect_relationship_fields(obj, parameters, adapter, relationship_fields)
        cls._apply_foreign_keys(obj, relationship_fields, adapter)
        cls._save_obj(obj, parameters)
        cls._apply_metadata_and_relationships(obj, relationship_fields, adapter)

    @classmethod
    def _init_relationship_fields(cls):
        """Return initial relationship tracking structures for ServiceNow models.

        Returns:
            Dictionary of relationship field containers.
        """
        return {
            "foreign_keys": defaultdict(dict),
            "many_to_many_fields": defaultdict(list),
            "custom_relationship_foreign_keys": defaultdict(dict),
            "custom_relationship_many_to_many_fields": defaultdict(dict),
            "object_metadata_fields": [],
        }

    @classmethod
    def _collect_relationship_fields(cls, obj, parameters, adapter, relationship_fields):
        """Collect relationship field values from parameters.

        Args:
            obj: Nautobot ORM object being updated.
            parameters: Field parameters to apply.
            relationship_fields: Relationship tracking dictionary.
            adapter: DiffSync adapter for lookups.
        """
        for field, value in parameters.items():
            cls._handle_single_field(field, obj, value, relationship_fields, adapter)

    @classmethod
    def _apply_foreign_keys(cls, obj, relationship_fields, adapter):
        """Apply foreign key relationships before saving.

        Args:
            obj: Nautobot ORM object being updated.
            relationship_fields: Relationship tracking dictionary.
            adapter: DiffSync adapter for lookups.
        """
        cls._lookup_and_set_foreign_keys(relationship_fields["foreign_keys"], obj, adapter)

    @classmethod
    def _save_obj(cls, obj, parameters):
        """Save an object and raise a CRUD error on validation failures.

        Args:
            obj: Nautobot ORM object being updated.
            parameters: Field parameters applied to the object.
        """
        try:
            obj.validated_save()
        except (ValidationError, ValueError, ObjectDoesNotExist) as error:
            safe_parameters = cls._serialize_parameters(parameters)
            raise ObjectCrudException(
                f"Validated save failed for Django object:\n{error}\nParameters: {safe_parameters}"
            ) from error

    @classmethod
    def _apply_metadata_and_relationships(cls, obj, relationship_fields, adapter):
        """Apply metadata and remaining relationships after save.

        Args:
            obj: Nautobot ORM object being updated.
            relationship_fields: Relationship tracking dictionary.
            adapter: DiffSync adapter for lookups.
        """
        cls._set_object_metadata_fields(relationship_fields["object_metadata_fields"], obj)
        cls._lookup_and_set_custom_relationship_foreign_keys(
            relationship_fields["custom_relationship_foreign_keys"], obj, adapter
        )
        cls._set_custom_relationship_to_many_fields(
            relationship_fields["custom_relationship_many_to_many_fields"], obj, adapter
        )
        cls._set_many_to_many_fields(relationship_fields["many_to_many_fields"], obj)

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
