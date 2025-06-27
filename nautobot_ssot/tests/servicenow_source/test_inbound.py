import json
from unittest.mock import MagicMock, patch

from nautobot.core.testing import TransactionTestCase
from nautobot.extras.models import JobResult

from nautobot_ssot.integrations.servicenow_source.diffsync import (
    ServiceNowDiffSync,
    load_mapping,
    create_model_class,
)
from nautobot_ssot.integrations.servicenow_source.jobs import ServiceNowDataSource


FIXTURE_PATH = "./nautobot_ssot/tests/servicenow_source/fixtures/servers.json"


class MappingTestCase(TransactionTestCase):
    """Validate dynamic model creation."""

    databases = ("default", "job_logs")

    def test_create_model_class(self):
        mapping = load_mapping("nautobot_ssot/integrations/servicenow_source/mapping.yml")
        table_map = mapping["cmdb_ci_server"]
        model_cls = create_model_class(
            "cmdb_ci_server",
            table_map["model"],
            table_map["identifiers"],
            table_map["attributes"],
        )
        obj = model_cls(name="example", serial="XYZ")
        self.assertEqual(obj.name, "example")
        self.assertEqual(obj.serial, "XYZ")


class ServiceNowDiffSyncTestCase(TransactionTestCase):
    """Ensure ServiceNowDiffSync loads data correctly."""

    databases = ("default", "job_logs")

    def setUp(self):
        with open(FIXTURE_PATH, encoding="utf-8") as handle:
            self.records = json.load(handle)

    def test_load(self):
        mapping = load_mapping("nautobot_ssot/integrations/servicenow_source/mapping.yml")

        class MockClient:
            def all_table_entries(self, table, query=None):  # pylint: disable=unused-argument
                if table == "cmdb_ci_server":
                    return self.records
                return []

        diff = ServiceNowDiffSync(mapping=mapping, client=MockClient(), job=MagicMock(), sync=None)
        diff.load()
        servers = diff.get_all("cmdb_ci_server")
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].name, "test-server-01")
        self.assertEqual(servers[0].serial, "ABC123")

    @patch("nautobot_ssot.integrations.servicenow_source.jobs.get_servicenow_parameters")
    @patch("nautobot_ssot.integrations.servicenow_source.jobs.ServiceNowClient")
    def test_job_load_source_adapter(self, mock_client_cls, mock_params):
        mock_params.return_value = {"instance": "dev", "username": "user", "password": "pass"}
        mock_client = MagicMock()
        mock_client.all_table_entries.return_value = self.records
        mock_client_cls.return_value = mock_client

        job = ServiceNowDataSource()
        job.job_result = JobResult.objects.create(name=job.class_path, task_name="test", worker="default")
        job.load_source_adapter()

        self.assertIsInstance(job.source_adapter, ServiceNowDiffSync)
        self.assertEqual(len(job.source_adapter.get_all("cmdb_ci_server")), 1)

