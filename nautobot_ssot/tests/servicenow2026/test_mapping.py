"""Tests for ServiceNow 2026 mapping helpers."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.servicenow import ServiceNowAdapter
from nautobot_ssot.integrations.servicenow2026.mapping import _extract_reference_value, map_record


class MappingHelpersTest(SimpleTestCase):
    """Test cases for mapping helpers."""

    def test_map_record_with_column_and_reference(self):
        """map_record handles column and reference mappings."""
        record = {
            "name": "HQ",
            "parent": {"value": "sys-parent"},
        }
        mappings = [
            {"field": "name", "column": "name"},
            {"field": "parent_sys_id", "reference": {"key": "parent"}},
        ]
        mapped = map_record(record, mappings)
        self.assertEqual(mapped["name"], "HQ")
        self.assertEqual(mapped["parent_sys_id"], "sys-parent")

    def test_map_record_with_transform(self):
        """map_record applies transforms."""
        record = {"latitude": "35.6749897"}
        mappings = [{"field": "latitude", "column": "latitude", "transform": "to_latitude"}]
        mapped = map_record(record, mappings)
        self.assertEqual(mapped["latitude"], Decimal("35.674990"))

    def test_extract_reference_value_blank_is_none(self):
        """_extract_reference_value normalizes blank values to None."""
        self.assertIsNone(_extract_reference_value(""))
        self.assertIsNone(_extract_reference_value("   "))
        self.assertIsNone(_extract_reference_value({"value": ""}))
        self.assertIsNone(_extract_reference_value({"sys_id": "   "}))

    def test_apply_defaults_sets_missing_values(self):
        """_apply_defaults fills missing/empty fields."""
        attributes = {"name": "HQ", "status__name": ""}
        defaults = {"status__name": "Active", "role__name": "Network"}
        ServiceNowAdapter.apply_defaults(attributes, defaults)
        self.assertEqual(attributes["status__name"], "Active")
        self.assertEqual(attributes["role__name"], "Network")

    def test_strip_suffix_removes_hex_suffix(self):
        """_strip_suffix removes known hex suffixes."""
        self.assertEqual(ServiceNowAdapter.strip_suffix("Unknown (deadbeef)"), "Unknown")
        self.assertEqual(ServiceNowAdapter.strip_suffix("Name (nothex)"), "Name (nothex)")

    @patch("nautobot_ssot.integrations.servicenow2026.diffsync.adapters.servicenow.metadata_utils.get_object_by_sys_id")
    def test_null_unresolved_references_clears_missing(self, get_object_by_sys_id):
        """_null_unresolved_references clears missing references."""
        adapter = ServiceNowAdapter(client=MagicMock(), job=MagicMock())
        adapter.loaded_sys_ids = {"company": {"tenant-1"}}
        get_object_by_sys_id.side_effect = lambda model, value: object() if value == "tenant-1" else None
        records = [
            {"tenant_sys_id": "tenant-1"},
            {"tenant_sys_id": "missing"},
            {"tenant_sys_id": ""},
        ]
        updated = adapter.null_unresolved_references("location", records)
        self.assertEqual(updated[0]["tenant_sys_id"], "tenant-1")
        self.assertIsNone(updated[1]["tenant_sys_id"])
        self.assertIsNone(updated[2]["tenant_sys_id"])
