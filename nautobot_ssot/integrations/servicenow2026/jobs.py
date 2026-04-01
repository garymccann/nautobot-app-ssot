"""Jobs for ServiceNow 2026 integration."""

from pathlib import Path
from typing import Any

from diffsync.enum import DiffSyncFlags
from django.templatetags.static import static
from nautobot.extras.jobs import BooleanVar, ChoiceVar, Job, ObjectVar, StringVar
from nautobot.extras.models import ExternalIntegration

from nautobot_ssot.integrations.servicenow2026.client import (
    ServiceNowBackendBase,
    ServiceNowClient,
)
from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.nautobot import TheNautobotAdapter
from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.servicenow import ServiceNowAdapter
from nautobot_ssot.integrations.servicenow2026.mapping import load_mapping
from nautobot_ssot.integrations.servicenow2026.utils.helpers import parse_csv
from nautobot_ssot.jobs.base import DataMapping, DataSource, DataTarget

name = "ServiceNow 2026 SSoT"  # pylint: disable=invalid-name


class _ServiceNow2BaseJob(Job):
    """Shared configuration for ServiceNow jobs."""

    def __init__(self):
        """Initialize job defaults."""
        self.debug: bool
        self.integration: ExternalIntegration
        self.backend: ServiceNowBackendBase
        super().__init__()

    debug_var = BooleanVar(description="Enable verbose logging.", default=False)

    servicenow_instance = ObjectVar(
        model=ExternalIntegration,
        queryset=ExternalIntegration.objects.filter(name="ServiceNow"),
        display_field="display",
        label="ServiceNow Instance",
        required=True,
    )
    backend_choice = ChoiceVar(
        choices=(
            ("pysnc", "PySNC"),
            ("pysnow", "PySnow"),
        ),
        default="pysnc",
        label="ServiceNow Client Backend",
    )

    # mapping_profile = ChoiceVar(
    #     choices=list_mapping_profiles(),
    #     required=False,
    #     description="Mapping profile to use for ServiceNow2 sync.",
    # )

    delete_records = BooleanVar(description="Delete records missing from the source.")

    mapping_data: dict[str, Any]

    @staticmethod
    def _build_client(integration: ExternalIntegration, backend: str) -> ServiceNowClient:
        """Prepare and return a ServiceNowClient instance based on the given integration and backend.

        Args:
            integration: ExternalIntegration instance.
            backend: Backend name (pysnc, pysnow).

        Returns:
            ServiceNowClient instance.
        """
        return ServiceNowClient(integration=integration, backend=backend)


# class ServiceNowSyncLogMixin:
#     """Mixin to ensure sync log diffs are JSON-serializable."""
#
#     @classmethod
#     def _serialize_log_value(cls, value):
#         """Return JSON-safe values for SyncLogEntry.diff.
#
#         Args:
#             value: Diff value to serialize.
#
#         Returns:
#             JSON-safe value.
#         """
#         if isinstance(value, Model):
#             return str(value)
#         if isinstance(value, dict):
#             return {key: cls._serialize_log_value(val) for key, val in value.items()}
#         if isinstance(value, (list, tuple, set)):
#             return [cls._serialize_log_value(item) for item in value]
#         return value
#
#     def sync_log(self, *args, **kwargs):
#         """Log a sync message with JSON-safe diff payloads.
#
#         Args:
#             *args: Positional args passed to sync_log.
#             **kwargs: Keyword args passed to sync_log.
#         """
#         if "diff" in kwargs:
#             diff = kwargs.get("diff")
#             if diff is not None:
#                 kwargs["diff"] = self._serialize_log_value(diff)
#         elif len(args) >= 4:
#             args = list(args)
#             if args[3] is not None:
#                 args[3] = self._serialize_log_value(args[3])
#         super().sync_log(*args, **kwargs)


class ServiceNowToNautobot(_ServiceNow2BaseJob, DataSource):  # pylint: disable=too-many-instance-attributes
    """Sync data from ServiceNow into Nautobot."""

    mapping_path = StringVar(
        label="Mapping file path",
        required=False,
        default="",
    )
    filter_mode = ChoiceVar(
        choices=(
            ("none", "None"),
            ("subtree", "Subtree"),
            ("types", "Types"),
            ("subtree+types", "Subtree + Types"),
        ),
        default="none",
        label="Location filter mode",
    )
    root_location_sys_id = StringVar(
        label="Root Location sys_id",
        required=False,
        default="",
    )
    location_types = StringVar(
        label="Location types (comma-separated)",
        required=False,
        default="",
    )
    include_unknown_type = BooleanVar(
        default=True,
        label="Include locations with unknown type",
    )

    def __init__(self):
        """Initialize job defaults."""
        super().__init__()
        self.mapping_defaults = {}

    class Meta:  # pylint: disable=too-few-public-methods
        """Metadata about this Job."""

        """Metadata about this ServiceNow Data Source Job."""

        name = "ServiceNow ⟹ Nautobot"
        data_source = "ServiceNow"
        data_source_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        data_target = "Nautobot"
        description = "Synchronize data from ServiceNow into Nautobot."
        has_sensitive_variables = False
        is_singleton = True
        soft_time_limit = 21600  # 6 hours
        time_limit = 86400  # 24 hours

    @classmethod
    def data_mappings(cls):
        """List describing the data mappings involved in this DataSource."""
        return (
            DataMapping("Company", "", "Tenant", ""),
            DataMapping("Manufacturer", "", "Manufacturer", ""),
            DataMapping("Device Model", "", "DeviceType", ""),
            DataMapping("Location", "", "Location", ""),
            DataMapping("Device", "", "Device", ""),
        )

    def load_source_adapter(self):
        """Load ServiceNow adapter."""
        mapping_path = Path(self.mapping_path) if self.mapping_path else None
        mapping = load_mapping(mapping_path)
        self.mapping_defaults = {name: entry.get("defaults", {}) for name, entry in mapping.items()}
        self.source_adapter = ServiceNowAdapter(
            client=self._build_client(self.integration, self.backend),
            job=self,
            mapping_path=mapping_path,
            filter_mode=self.filter_mode,
            location_types=parse_csv(self.location_types),
            include_unknown_type=self.include_unknown_type,
            root_location_sys_id=self.root_location_sys_id or None,
        )
        self.source_adapter.load()

    def load_target_adapter(self):
        """Load Nautobot adapter."""
        self.target_adapter = TheNautobotAdapter(job=self, sync=self.sync)
        self.target_adapter.load()

    def run(  # pylint: disable=arguments-differ
        self,
        dryrun,
        memory_profiling,
        *args,
        **kwargs,
    ):
        """Run sync.

        Args:
            dryrun: Whether to run in dry-run mode.
            memory_profiling: Whether to collect memory profiling data.
        """
        self.dryrun = dryrun
        self.memory_profiling = memory_profiling
        self.integration = kwargs.get("servicenow_instance", ExternalIntegration)
        self.backend = kwargs.get("backend_choice")
        self.mapping_path = kwargs.get("mapping_path")
        self.filter_mode = kwargs.get("filter_mode")
        self.root_location_sys_id = kwargs.get("root_location_sys_id")
        self.location_types = kwargs.get("location_types")
        self.include_unknown_type = kwargs.get("include_unknown_type")
        self.debug = kwargs.get("debug")
        super().run(dryrun=dryrun, memory_profiling=memory_profiling, *args, **kwargs)


class NautobotToServiceNow(_ServiceNow2BaseJob, DataTarget):  # pylint: disable=too-many-instance-attributes
    """Sync data from Nautobot into ServiceNow."""

    mapping_path = StringVar(
        label="Mapping file path",
        required=False,
        default="",
    )
    delete_records = BooleanVar(
        description="Delete ServiceNow records not present in Nautobot.",
        default=False,
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Metadata about this Job."""

        name = "Nautobot ⟹ ServiceNow"
        data_target = "ServiceNow"
        data_target_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        description = "Synchronize data from Nautobot into ServiceNow."
        has_sensitive_variables = False
        is_singleton = True
        soft_time_limit = 21600  # 6 hours
        time_limit = 86400  # 24 hours

    @classmethod
    def data_mappings(cls):
        """List describing the data mappings involved in this DataTarget."""
        return (
            DataMapping("Tenant", "", "Company", ""),
            DataMapping("Manufacturer", "", "Manufacturer", ""),
            DataMapping("DeviceType", "", "Device Model", ""),
            DataMapping("Location", "", "Location", ""),
            DataMapping("Device", "", "Device", ""),
        )

    def load_source_adapter(self):
        """Load Nautobot adapter."""
        self.source_adapter = TheNautobotAdapter(job=self, sync=self.sync, include_without_sys_id=True)
        self.source_adapter.load()

    def load_target_adapter(self):
        """Load ServiceNow adapter."""
        mapping_path = Path(self.mapping_path) if self.mapping_path else None
        mapping = load_mapping(mapping_path)
        self.mapping_defaults = {name: entry.get("defaults", {}) for name, entry in mapping.items()}
        client = self._build_client(self.integration, self.backend)
        self.target_adapter = ServiceNowAdapter(
            client=client,
            job=self,
            mapping_path=mapping_path
        )
        self.target_adapter.load()

    def run(  # pylint: disable=arguments-differ
        self,
        dryrun,
        memory_profiling,
        *args,
        **kwargs,
    ):
        """Run sync.

        Args:
            dryrun: Whether to run in dry-run mode.
            memory_profiling: Whether to collect memory profiling data.
        """
        self.dryrun = dryrun
        self.memory_profiling = memory_profiling
        self.integration = kwargs.get("servicenow_instance", ExternalIntegration)
        self.backend = kwargs.get("backend_choice")
        self.mapping_path = kwargs.get("mapping_path")
        self.delete_records = kwargs.get("delete_records")
        self.debug = kwargs.get("debug")
        if not self.delete_records:
            self.diffsync_flags |= DiffSyncFlags.SKIP_UNMATCHED_DST
        super().run(dryrun=dryrun, memory_profiling=memory_profiling, *args, **kwargs)


jobs = [ServiceNowToNautobot, NautobotToServiceNow]
