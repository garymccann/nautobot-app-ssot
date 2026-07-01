"""Tests for ServiceNow 2026 client helpers."""

from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices

from nautobot_ssot.integrations.servicenow2026 import client as client_module
from nautobot_ssot.integrations.servicenow2026.client import (
    PySnowBackend,
    ServiceNowClient,
    ServiceNowClientError,
)


def _make_integration(
    remote_url="https://example.service-now.com",
    token="token",
    username=None,
    password=None,
    verify_ssl=True,
):
    secrets_group = MagicMock()
    secrets_group.access_type = SecretsGroupAccessTypeChoices.TYPE_HTTP

    def get_secret_value(access_type=None, secret_type=None, **_kwargs):
        if secret_type == SecretsGroupSecretTypeChoices.TYPE_TOKEN:
            return token
        if secret_type == SecretsGroupSecretTypeChoices.TYPE_USERNAME:
            return username
        if secret_type == SecretsGroupSecretTypeChoices.TYPE_PASSWORD:
            return password
        return None

    secrets_group.get_secret_value.side_effect = get_secret_value
    return SimpleNamespace(
        remote_url=remote_url,
        verify_ssl=verify_ssl,
        secrets_group=secrets_group,
    )


class ServiceNowClientTest(SimpleTestCase):
    """Test cases for ServiceNowClient backend selection."""

    def test_pysnow_backend_selected(self):
        """Explicit PySnow backend selection initializes PySnow backend."""
        integration = _make_integration()
        client = ServiceNowClient(integration=integration, backend="pysnow")
        self.assertIsInstance(client.backend, PySnowBackend)

    def test_invalid_backend_raises(self):
        """Invalid backend names raise a config error."""
        integration = _make_integration()
        with self.assertRaises(ServiceNowClientError):
            ServiceNowClient(integration=integration, backend="auto")

    def test_pysnc_backend_unavailable(self):
        """PySNC backend raises when pysnc is not installed."""
        if find_spec("pysnc") is not None:
            self.skipTest("pysnc is installed; unavailable test is not applicable.")
        integration = _make_integration()
        with self.assertRaises(ServiceNowClientError):
            ServiceNowClient(integration=integration, backend="pysnc")
