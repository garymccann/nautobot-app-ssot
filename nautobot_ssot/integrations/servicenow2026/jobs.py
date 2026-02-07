"""Jobs for ServiceNow 2026 integration."""

from pathlib import Path
from typing import List, Optional

from diffsync.enum import DiffSyncFlags
from django.core.exceptions import ObjectDoesNotExist
from django.templatetags.static import static
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.jobs import BooleanVar, ChoiceVar, ObjectVar, StringVar
from nautobot.extras.models import ExternalIntegration

from nautobot_ssot.integrations.servicenow2026.client import ServiceNowClient, ServiceNowConfig
from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.nautobot import TheNautobotAdapter
from nautobot_ssot.integrations.servicenow2026.diffsync.adapters.servicenow import ServiceNowAdapter
from nautobot_ssot.integrations.servicenow2026.mapping import load_mapping
from nautobot_ssot.jobs.base import DataMapping, DataSource, DataTarget

name = "ServiceNow 2026 SSoT"  # pylint: disable=invalid-name


class JobConfigError(Exception):
    """Custom exception for invalid job configuration."""


def _parse_csv(value: Optional[str]) -> List[str]:
    """Return a list of values from a comma-delimited string.

    Args:
        value: Comma-delimited string.

    Returns:
        List of stripped values.
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_secret_value(integration: ExternalIntegration, secret_type: str) -> Optional[str]:
    """Return a secret value from an ExternalIntegration secrets group.

    Args:
        integration: ExternalIntegration instance.
        secret_type: Secret type identifier.

    Returns:
        Secret value if available, otherwise None.
    """
    if not integration.secrets_group:
        return None
    try:
        return integration.secrets_group.get_secret_value(
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=secret_type,
        )
    except ObjectDoesNotExist:
        return None


def _build_config(integration: ExternalIntegration) -> ServiceNowConfig:
    """Build a ServiceNowConfig from an ExternalIntegration.

    Args:
        integration: ExternalIntegration instance.

    Returns:
        ServiceNowConfig instance.

    Raises:
        JobConfigError: If credentials are missing.
    """
    token = _get_secret_value(integration, SecretsGroupSecretTypeChoices.TYPE_TOKEN)
    username = _get_secret_value(integration, SecretsGroupSecretTypeChoices.TYPE_USERNAME)
    password = _get_secret_value(integration, SecretsGroupSecretTypeChoices.TYPE_PASSWORD)

    if not token and not (username and password):
        raise JobConfigError("External Integration must provide token or username/password secrets.")

    return ServiceNowConfig(
        base_url=integration.remote_url,
        username=username,
        password=password,
        token=token,
        verify_ssl=integration.verify_ssl,
    )


def _build_client(integration: ExternalIntegration, backend: str) -> ServiceNowClient:
    """Build a ServiceNowClient for job execution.

    Args:
        integration: ExternalIntegration instance.
        backend: Backend name (auto, pysnc, pysnow).

    Returns:
        ServiceNowClient instance.
    """
    return ServiceNowClient(config=_build_config(integration), backend=backend)


class ServiceNowToNautobot(DataSource):  # pylint: disable=too-many-instance-attributes
    """Sync data from ServiceNow into Nautobot."""

    integration = ObjectVar(
        model=ExternalIntegration,
        queryset=ExternalIntegration.objects.all(),
        display_field="display",
        label="ServiceNow Instance",
        required=True,
    )
    backend = ChoiceVar(
        choices=(
            ("auto", "Auto"),
            ("pysnc", "PySNC"),
            ("pysnow", "PySnow"),
        ),
        default="pysnc",
        label="ServiceNow Client Backend",
    )
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
    debug = BooleanVar(description="Enable verbose logging.", default=False)

    def __init__(self):
        """Initialize job defaults."""
        super().__init__()
        self.mapping_defaults = {}

    class Meta:  # pylint: disable=too-few-public-methods
        """Metadata about this Job."""

        name = "ServiceNow_to_Nautobot"
        data_source = "ServiceNow"
        data_source_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        description = "Synchronize data from ServiceNow into Nautobot."
        has_sensitive_variables: bool = False

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
        client = _build_client(self.integration, self.backend)
        self.source_adapter = ServiceNowAdapter(
            client=client,
            job=self,
            mapping_path=mapping_path,
            filter_mode=self.filter_mode,
            location_types=_parse_csv(self.location_types),
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
        integration,
        backend,
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
        self.integration = integration
        self.backend = backend
        self.mapping_path = kwargs.get("mapping_path")
        self.filter_mode = kwargs.get("filter_mode")
        self.root_location_sys_id = kwargs.get("root_location_sys_id")
        self.location_types = kwargs.get("location_types")
        self.include_unknown_type = kwargs.get("include_unknown_type")
        self.debug = kwargs.get("debug")
        super().run(dryrun=dryrun, memory_profiling=memory_profiling, *args, **kwargs)


class NautobotToServiceNow(DataTarget):  # pylint: disable=too-many-instance-attributes
    """Sync data from Nautobot into ServiceNow."""

    integration = ObjectVar(
        model=ExternalIntegration,
        queryset=ExternalIntegration.objects.all(),
        display_field="display",
        label="ServiceNow Instance",
        required=True,
    )
    backend = ChoiceVar(
        choices=(
            ("auto", "Auto"),
            ("pysnc", "PySNC"),
            ("pysnow", "PySnow"),
        ),
        default="pysnc",
        label="ServiceNow Client Backend",
    )
    mapping_path = StringVar(
        label="Mapping file path",
        required=False,
        default="",
    )
    delete_records = BooleanVar(
        description="Delete ServiceNow records not present in Nautobot.",
        default=False,
    )
    debug = BooleanVar(description="Enable verbose logging.", default=False)

    class Meta:  # pylint: disable=too-few-public-methods
        """Metadata about this Job."""

        name = "Nautobot_to_ServiceNow"
        data_target = "ServiceNow"
        data_target_icon = static("nautobot_ssot_servicenow/ServiceNow_logo.svg")
        description = "Synchronize data from Nautobot into ServiceNow."

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
        self.source_adapter = TheNautobotAdapter(job=self, sync=self.sync)
        self.source_adapter.load()

    def load_target_adapter(self):
        """Load ServiceNow adapter."""
        mapping_path = Path(self.mapping_path) if self.mapping_path else None
        if mapping_path:
            load_mapping(mapping_path)
        client = _build_client(self.integration, self.backend)
        self.target_adapter = ServiceNowAdapter(client=client, job=self, mapping_path=mapping_path)
        self.target_adapter.load()

    def run(  # pylint: disable=arguments-differ
        self,
        dryrun,
        memory_profiling,
        integration,
        backend,
        *args,
        **kwargs,
    ):
        """Run sync.

        Args:
            dryrun: Whether to run in dry-run mode.
            memory_profiling: Whether to collect memory profiling data.
            delete_records: Whether to delete unmatched ServiceNow records.
        """
        self.dryrun = dryrun
        self.memory_profiling = memory_profiling
        self.integration = integration
        self.backend = backend
        self.mapping_path = kwargs.get("mapping_path")
        self.delete_records = kwargs.get("delete_records")
        self.debug = kwargs.get("debug")
        if not self.delete_records:
            self.diffsync_flags |= DiffSyncFlags.SKIP_UNMATCHED_DST
        super().run(dryrun=dryrun, memory_profiling=memory_profiling, *args, **kwargs)


jobs = [ServiceNowToNautobot, NautobotToServiceNow]
