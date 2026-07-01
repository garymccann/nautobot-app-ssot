"""Template extensions for the ServiceNow 2026 integration."""

from django.template.loader import render_to_string
from nautobot.extras.plugins import TemplateExtension

from nautobot_ssot.integrations.servicenow2026.utils.metadata import get_servicenow_url
from nautobot_ssot.template_content import JobResultSyncLink

# pylint: disable=abstract-method


class ServiceNowObjectLinkBase(TemplateExtension):
    """Base template extension for ServiceNow object links."""

    def buttons(self):  # pylint: disable=arguments-differ
        """Inject a ServiceNow button into the object detail view, if applicable.

        Returns:
            HTML string for the button or an empty string.
        """
        obj = self.context.get("object")
        if not obj:
            return ""
        url = get_servicenow_url(obj)
        if not url:
            return ""
        return render_to_string(
            "nautobot_ssot_servicenow2026/servicenow_object_button.html",
            {"url": url},
        )


class ServiceNowDeviceLink(ServiceNowObjectLinkBase):
    """ServiceNow link for Device objects."""

    model = "dcim.device"


class ServiceNowDeviceTypeLink(ServiceNowObjectLinkBase):
    """ServiceNow link for DeviceType objects."""

    model = "dcim.devicetype"


class ServiceNowLocationLink(ServiceNowObjectLinkBase):
    """ServiceNow link for Location objects."""

    model = "dcim.location"


class ServiceNowManufacturerLink(ServiceNowObjectLinkBase):
    """ServiceNow link for Manufacturer objects."""

    model = "dcim.manufacturer"


class ServiceNowPlatformLink(ServiceNowObjectLinkBase):
    """ServiceNow link for Platform objects."""

    model = "dcim.platform"


template_extensions = [
    JobResultSyncLink,
    ServiceNowDeviceLink,
    ServiceNowDeviceTypeLink,
    ServiceNowLocationLink,
    ServiceNowManufacturerLink,
    ServiceNowPlatformLink,
]
