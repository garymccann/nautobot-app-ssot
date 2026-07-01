"""Tests for ServiceNow 2026 helper utilities."""

from django.test import SimpleTestCase

from nautobot_ssot.integrations.servicenow2026.utils.helpers import parse_csv


class ParseCsvTest(SimpleTestCase):
    """Test cases for parse_csv helper."""

    def test_parse_csv_empty_value(self):
        """Empty or None values should return an empty list."""
        self.assertEqual(parse_csv(None), [])
        self.assertEqual(parse_csv(""), [])

    def test_parse_csv_strips_and_ignores_empty_items(self):
        """Items should be stripped and empty values dropped."""
        self.assertEqual(parse_csv("a, b , ,c,, "), ["a", "b", "c"])
