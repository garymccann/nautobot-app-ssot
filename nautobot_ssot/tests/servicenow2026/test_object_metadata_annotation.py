"""Tests for ObjectMetadataAnnotation support."""

from typing import Annotated, Optional
from unittest.mock import MagicMock

from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase
from nautobot.extras.models.metadata import MetadataType, MetadataTypeDataTypeChoices, ObjectMetadata
from nautobot.tenancy.models import Tenant

from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.nautobot import TheNautobotAdapter
from nautobot_ssot.integrations.servicenow2026.diffsync.models import ServiceNowBaseModel
from nautobot_ssot.integrations.servicenow2026.object_metadata import ObjectMetadataAnnotation


class MetadataTenant(ServiceNowBaseModel):
    """Tenant model with ObjectMetadataAnnotation for testing."""

    _model = Tenant
    _modelname = "tenant"
    _identifiers = ("name",)
    _attributes = ("servicenow_sys_id",)

    name: str
    servicenow_sys_id: Annotated[Optional[str], ObjectMetadataAnnotation(key="ServiceNow Sys ID")] = None


class MetadataAdapter(TheNautobotAdapter):
    """Adapter for testing ObjectMetadataAnnotation behavior."""

    top_level = ("tenant",)
    tenant = MetadataTenant


class ObjectMetadataAnnotationTest(TestCase):
    """Test cases for ObjectMetadataAnnotation support."""

    @classmethod
    def setUpTestData(cls):
        cls.metadata_type, _ = MetadataType.objects.get_or_create(
            name="ServiceNow Sys ID", defaults={"data_type": MetadataTypeDataTypeChoices.TYPE_TEXT}
        )
        cls.metadata_type.content_types.add(ContentType.objects.get_for_model(Tenant))

    def test_load_object_metadata_value(self):
        """Adapter loads ObjectMetadata values into DiffSync models."""
        tenant = Tenant.objects.create(name="Tenant A")
        ObjectMetadata.objects.create(
            assigned_object=tenant,
            metadata_type=self.metadata_type,
            value="sys-123",
            scoped_fields=[],
        )
        adapter = MetadataAdapter(job=MagicMock())
        adapter.load()
        loaded = adapter.get("tenant", "Tenant A")
        self.assertEqual(loaded.servicenow_sys_id, "sys-123")

    def test_write_object_metadata_value(self):
        """DiffSync create writes ObjectMetadata values."""
        adapter = MetadataAdapter(job=MagicMock())
        MetadataTenant.create(adapter, ids={"name": "Tenant B"}, attrs={"servicenow_sys_id": "sys-456"})
        tenant = Tenant.objects.get(name="Tenant B")
        metadata = ObjectMetadata.objects.get(
            assigned_object_id=tenant.id,
            assigned_object_type=ContentType.objects.get_for_model(tenant),
            metadata_type=self.metadata_type,
        )
        self.assertEqual(metadata.value, "sys-456")


class ObjectMetadataAnnotationInitTest(SimpleTestCase):
    """Test cases for ObjectMetadataAnnotation __post_init__ handling."""

    def test_post_init_with_key(self):
        """__post_init__ keeps provided key."""
        annotation = ObjectMetadataAnnotation(key="ServiceNow Sys ID")
        self.assertEqual(annotation.key, "ServiceNow Sys ID")

    def test_post_init_with_legacy_name(self):
        """__post_init__ maps legacy name to key."""
        annotation = ObjectMetadataAnnotation(name="Legacy Name")
        self.assertEqual(annotation.key, "Legacy Name")

    def test_post_init_requires_key_or_name(self):
        """__post_init__ raises ValueError when key/name are missing."""
        with self.assertRaises(ValueError):
            ObjectMetadataAnnotation()
