"""Signal handlers for ServiceNow 2026 integration."""

from nautobot.core.signals import nautobot_database_ready
from nautobot.extras.models.metadata import MetadataTypeDataTypeChoices

from nautobot_ssot.integrations.servicenow2026 import constants


def register_signals(sender):
    """Register signals for ServiceNow 2026 integration."""
    nautobot_database_ready.connect(nautobot_database_ready_callback, sender=sender)


def nautobot_database_ready_callback(sender, *, apps, **kwargs):  # pylint: disable=unused-argument
    """Create ServiceNow metadata types when Nautobot is ready."""
    content_type_model = apps.get_model("contenttypes", "ContentType")
    metadata_type_model = apps.get_model("extras", "MetadataType")

    device_model = apps.get_model("dcim", "Device")
    device_type_model = apps.get_model("dcim", "DeviceType")
    location_model = apps.get_model("dcim", "Location")
    manufacturer_model = apps.get_model("dcim", "Manufacturer")
    platform_model = apps.get_model("dcim", "Platform")
    tenant_model = apps.get_model("tenancy", "Tenant")

    content_types = [
        content_type_model.objects.get_for_model(device_model),
        content_type_model.objects.get_for_model(device_type_model),
        content_type_model.objects.get_for_model(location_model),
        content_type_model.objects.get_for_model(manufacturer_model),
        content_type_model.objects.get_for_model(platform_model),
        content_type_model.objects.get_for_model(tenant_model),
    ]

    for metadata_name in constants.SERVICENOW_METADATA_TYPES:
        metadata_type, _ = metadata_type_model.objects.get_or_create(
            name=metadata_name,
            defaults={
                "data_type": MetadataTypeDataTypeChoices.TYPE_TEXT,
                "description": f"ServiceNow metadata for {metadata_name}.",
            },
        )
        for content_type in content_types:
            metadata_type.content_types.add(content_type)
