"""ServiceNow to Nautobot sync job."""

from diffsync.enum import DiffSyncFlags
from django.templatetags.static import static
from nautobot.extras.jobs import Job

from nautobot_ssot.jobs.base import DataMapping, DataSource
from nautobot_ssot.integrations.servicenow.utils import get_servicenow_parameters

from .servicenow import ServiceNowClient
from .diffsync import NautobotDynamicAdapter, ServiceNowDiffSync, load_mapping

name = "SSoT - ServiceNow Inbound"  # pylint: disable=invalid-name


class ServiceNowDataSource(DataSource, Job):  # pylint: disable=abstract-method
    """Job syncing data from ServiceNow into Nautobot."""

    class Meta:
        name = "ServiceNow ➜ Nautobot"
        data_source = "ServiceNow"
        data_source_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        description = "Synchronize data from ServiceNow into Nautobot."

    @classmethod
    def data_mappings(cls):
        mapping = load_mapping("nautobot_ssot/integrations/servicenow_source/mapping.yml")
        result = []
        for table, table_map in mapping.items():
            result.append(DataMapping(table_map["model"], None, table, None))
        return tuple(result)

    @classmethod
    def config_information(cls):
        configs = get_servicenow_parameters()
        return {
            "ServiceNow instance": configs.get("instance"),
            "Username": configs.get("username"),
        }

    def load_source_adapter(self):
        configs = get_servicenow_parameters()
        client = ServiceNowClient(
            instance=configs.get("instance"),
            username=configs.get("username"),
            password=configs.get("password"),
        )
        mapping = load_mapping("nautobot_ssot/integrations/servicenow_source/mapping.yml")
        self.source_adapter = ServiceNowDiffSync(mapping=mapping, job=self, sync=self.sync, client=client)
        self.source_adapter.load()

    def load_target_adapter(self):
        mapping = load_mapping("nautobot_ssot/integrations/servicenow_source/mapping.yml")
        self.target_adapter = NautobotDynamicAdapter(mapping=mapping, job=self, sync=self.sync)
        self.target_adapter.load()

    def lookup_object(self, model_name, unique_id):
        return None


jobs = [ServiceNowDataSource]

