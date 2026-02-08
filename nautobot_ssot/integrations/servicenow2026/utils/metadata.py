"""ServiceNow 2026 ObjectMetadata helpers."""

from typing import Optional

from django.contrib.contenttypes.models import ContentType
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata

from nautobot_ssot.integrations.servicenow2026 import constants


def get_metadata_type(name: str) -> Optional[MetadataType]:
    """Return a MetadataType by name if it exists.

    Args:
        name: MetadataType name to look up.

    Returns:
        MetadataType if found, otherwise None.
    """
    return MetadataType.objects.filter(name=name).first()


def get_object_metadata_value(obj, metadata_type_name: str) -> Optional[str]:
    """Return a metadata value for an object and metadata type name.

    Args:
        obj: Nautobot ORM object to read metadata from.
        metadata_type_name: Name of the MetadataType to look up.

    Returns:
        Metadata value if found, otherwise None.
    """
    metadata_type = get_metadata_type(metadata_type_name)
    if not metadata_type:
        return None
    content_type = ContentType.objects.get_for_model(type(obj))
    metadata = ObjectMetadata.objects.filter(
        assigned_object_id=obj.id,
        assigned_object_type=content_type,
        metadata_type=metadata_type,
    ).first()
    if not metadata:
        return None
    return metadata.value


def get_object_by_sys_id(model, sys_id: Optional[str]):
    """Return a Nautobot object by ServiceNow sys_id metadata value.

    Args:
        model: Django model class to resolve.
        sys_id: ServiceNow sys_id to match.

    Returns:
        Nautobot object if found, otherwise None.
    """
    if not sys_id:
        return None
    metadata_type = get_metadata_type(constants.SERVICENOW_METADATA_SYS_ID)
    if not metadata_type:
        return None
    content_type = ContentType.objects.get_for_model(model)
    metadata = (
        ObjectMetadata.objects.filter(
            metadata_type=metadata_type,
            assigned_object_type=content_type,
            _value=sys_id,
        )
        .select_related("assigned_object_type")
        .first()
    )
    if not metadata:
        return None
    return metadata.assigned_object


def build_servicenow_url(instance: Optional[str], table: Optional[str], sys_id: Optional[str]) -> Optional[str]:
    """Build a ServiceNow URL for the given instance, table, and sys_id.

    Args:
        instance: ServiceNow instance name or base URL.
        table: ServiceNow table name.
        sys_id: ServiceNow sys_id.

    Returns:
        URL string if inputs are complete, otherwise None.
    """
    if not instance or not table or not sys_id:
        return None
    instance = instance.rstrip("/")
    return f"{instance}/nav_to.do?uri={table}.do?sys_id={sys_id}"


def get_servicenow_url(obj) -> Optional[str]:
    """Return a ServiceNow URL for an object if metadata is present.

    Args:
        obj: Nautobot ORM object to inspect.

    Returns:
        ServiceNow URL if available, otherwise None.
    """
    url_value = get_object_metadata_value(obj, constants.SERVICENOW_METADATA_URL)
    if url_value:
        return url_value
    sys_id = get_object_metadata_value(obj, constants.SERVICENOW_METADATA_SYS_ID)
    table = get_object_metadata_value(obj, constants.SERVICENOW_METADATA_TABLE)
    instance = get_object_metadata_value(obj, constants.SERVICENOW_METADATA_INSTANCE)
    return build_servicenow_url(instance=instance, table=table, sys_id=sys_id)
