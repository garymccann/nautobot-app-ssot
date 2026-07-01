"""Tests for ServiceNow 2026 metadata utilities."""

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.dcim.models import Location, LocationType
from nautobot.extras.models import Status
from nautobot.extras.models.metadata import MetadataType, MetadataTypeDataTypeChoices, ObjectMetadata

from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.utils import metadata as metadata_utils


class ServiceNowMetadataUtilsTest(TestCase):
    """Test cases for metadata utility functions."""

    @classmethod
    def setUpTestData(cls):
        cls.location_type, _ = LocationType.objects.get_or_create(name="Site")
        status_active = Status.objects.get(name="Active")
        cls.location = Location.objects.create(
            name="HQ",
            location_type=cls.location_type,
            status=status_active,
        )

        cls.metadata_types = {}
        for name in constants.SERVICENOW_METADATA_TYPES:
            metadata_type, _ = MetadataType.objects.get_or_create(
                name=name,
                defaults={"data_type": MetadataTypeDataTypeChoices.TYPE_TEXT},
            )
            metadata_type.content_types.add(ContentType.objects.get_for_model(Location))
            cls.metadata_types[name] = metadata_type

    def test_get_servicenow_url_prefers_metadata(self):
        """get_servicenow_url returns stored URL when available."""
        ObjectMetadata.objects.create(
            assigned_object=self.location,
            metadata_type=self.metadata_types[constants.SERVICENOW_METADATA_URL],
            value="https://example.service-now.com/nav_to.do?uri=cmn_location.do?sys_id=abc",
            scoped_fields=[],
        )
        url = metadata_utils.get_servicenow_url(self.location)
        self.assertTrue(url.startswith("https://example.service-now.com"))

    def test_get_servicenow_url_without_url_metadata(self):
        """get_servicenow_url returns None when URL metadata is absent."""
        ObjectMetadata.objects.create(
            assigned_object=self.location,
            metadata_type=self.metadata_types[constants.SERVICENOW_METADATA_SYS_ID],
            value="abc",
            scoped_fields=[],
        )
        url = metadata_utils.get_servicenow_url(self.location)
        self.assertIsNone(url)
