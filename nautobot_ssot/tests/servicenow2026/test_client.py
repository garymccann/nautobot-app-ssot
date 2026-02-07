"""Tests for ServiceNow 2026 client helpers."""

from unittest.mock import patch

from django.test import SimpleTestCase

from nautobot_ssot.integrations.servicenow2026.client import (
    PySnowBackend,
    ServiceNowClient,
    ServiceNowConfig,
)


class ServiceNowConfigTest(SimpleTestCase):
    """Test cases for ServiceNowConfig helpers."""

    def test_resolve_instance_from_base_url(self):
        """resolve_instance derives instance from base URL."""
        config = ServiceNowConfig(base_url="https://example.service-now.com")
        self.assertEqual(config.resolve_instance(), "example")

    def test_resolve_base_url_from_instance(self):
        """resolve_base_url derives base URL from instance."""
        config = ServiceNowConfig(instance="example")
        self.assertEqual(config.resolve_base_url(), "https://example.service-now.com")


class ServiceNowClientTest(SimpleTestCase):
    """Test cases for ServiceNowClient backend selection."""

    def test_auto_backend_falls_back_to_pysnow(self):
        """Auto backend falls back to PySnow when PySNC is unavailable."""
        config = ServiceNowConfig(base_url="https://example.service-now.com", token="token")
        with patch("nautobot_ssot.integrations.servicenow2026.client._resolve_pysnc_client_class", return_value=None):
            client = ServiceNowClient(config=config, backend="auto")
        self.assertIsInstance(client.backend, PySnowBackend)
