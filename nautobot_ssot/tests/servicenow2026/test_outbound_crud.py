"""Tests for ServiceNow 2026 outbound CRUD metadata persistence."""

from types import SimpleNamespace

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.extras.models.metadata import MetadataType, MetadataTypeDataTypeChoices, ObjectMetadata
from nautobot.tenancy.models import Tenant

from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.diffsync.models import Company


class OutboundCrudMetadataPersistenceTest(TestCase):
    """Test metadata persistence after outbound ServiceNow create operations."""

    @classmethod
    def setUpTestData(cls):
        tenant_content_type = ContentType.objects.get_for_model(Tenant)
        for metadata_name in (constants.SERVICENOW_METADATA_SYS_ID, constants.SERVICENOW_METADATA_URL):
            metadata_type, _ = MetadataType.objects.get_or_create(
                name=metadata_name,
                defaults={"data_type": MetadataTypeDataTypeChoices.TYPE_TEXT},
            )
            metadata_type.content_types.add(tenant_content_type)

    def test_persist_created_servicenow_metadata(self):
        """Created ServiceNow sys_id and URL are written back to Nautobot metadata."""
        tenant = Tenant.objects.create(name="Tenant Outbound Create")
        adapter = SimpleNamespace(
            client=SimpleNamespace(integration=SimpleNamespace(remote_url="https://example.service-now.com"))
        )

        created_url = Company._persist_created_servicenow_metadata(
            adapter=adapter,
            identifiers={"servicenow_sys_id": f"nautobot-{tenant.pk}"},
            table="core_company",
            created_sys_id="sys-new-123",
        )

        tenant_content_type = ContentType.objects.get_for_model(Tenant)
        sys_id_metadata = ObjectMetadata.objects.get(
            assigned_object_id=tenant.id,
            assigned_object_type=tenant_content_type,
            metadata_type__name=constants.SERVICENOW_METADATA_SYS_ID,
        )
        url_metadata = ObjectMetadata.objects.get(
            assigned_object_id=tenant.id,
            assigned_object_type=tenant_content_type,
            metadata_type__name=constants.SERVICENOW_METADATA_URL,
        )

        self.assertEqual(sys_id_metadata.value, "sys-new-123")
        self.assertEqual(created_url, "https://example.service-now.com/nav_to.do?uri=core_company.do?sys_id=sys-new-123")
        self.assertEqual(url_metadata.value, created_url)
