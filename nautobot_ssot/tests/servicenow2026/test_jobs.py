"""Integration tests for ServiceNow 2026 SSoT jobs using fixture data."""

import ast
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TransactionTestCase, override_settings
from nautobot.apps.testing import run_job_for_testing
from nautobot.dcim.models import Device, DeviceType, Location, LocationType, Manufacturer
from nautobot.extras.choices import JobResultStatusChoices, SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import (
    ExternalIntegration,
    Job,
    JobResult,
    Role,
    Secret,
    SecretsGroup,
    SecretsGroupAssociation,
    Status,
)
from nautobot.extras.models.metadata import MetadataType, MetadataTypeDataTypeChoices, ObjectMetadata
from nautobot.tenancy.models import Tenant

from nautobot_ssot.integrations.servicenow2026 import constants
from nautobot_ssot.integrations.servicenow2026.diffsync.models import UNASSIGNED_LOCATION_NAME
from nautobot_ssot.integrations.servicenow2026.jobs import NautobotToServiceNow
from nautobot_ssot.models import Sync

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "Zurich_Patch5"
FIXTURE_TABLES = {
    constants.SERVICENOW_TABLE_COMPANY: "companies.json",
    constants.SERVICENOW_TABLE_MODEL: "models.json",
    constants.SERVICENOW_TABLE_LOCATION: "locations.json",
    constants.SERVICENOW_TABLE_DEVICE: "devices.json",
}


class FixtureServiceNowClient:
    """ServiceNow client stub that returns fixture records."""

    def __init__(self, records: Dict[str, List[dict]], integration: ExternalIntegration):
        self.records = records
        self.integration = integration
        self.queries: Dict[str, List[dict]] = defaultdict(list)

    def iter_table(self, table: str, query: Optional[Dict[str, object]] = None) -> Iterable[dict]:
        query = query or {}
        self.queries[table].append(query)
        records = list(self.records.get(table, []))
        if query.get("manufacturer"):
            records = [
                record
                for record in records
                if str(record.get("manufacturer") or "").strip().lower() in {"true", "1", "yes", "y", "on"}
            ]
        for record in records:
            yield record


@override_settings(PLUGINS_CONFIG={"nautobot_ssot": {"enable_servicenow2026": True}})
class ServiceNow2026JobIntegrationTest(TransactionTestCase):
    """Integration tests for ServiceNow 2026 jobs."""

    databases = ("default", "job_logs")

    def setUp(self):
        """Set up shared fixtures and Nautobot prerequisites."""
        super().setUp()
        self.fixture_records = {}
        for table, filename in FIXTURE_TABLES.items():
            path = FIXTURE_DIR / filename
            with path.open("r", encoding="utf-8") as handle:
                self.fixture_records[table] = json.load(handle)
        self.status_active = Status.objects.get_or_create(name="Active")[0]
        self.role_admin = Role.objects.get_or_create(name="Administrative")[0]
        self.location_type = LocationType.objects.get_or_create(name="Demo", defaults={"nestable": True})[0]

        device_ct = ContentType.objects.get_for_model(Device)
        location_ct = ContentType.objects.get_for_model(Location)
        if device_ct not in self.status_active.content_types.all():
            self.status_active.content_types.add(device_ct)
        if location_ct not in self.status_active.content_types.all():
            self.status_active.content_types.add(location_ct)
        if device_ct not in self.role_admin.content_types.all():
            self.role_admin.content_types.add(device_ct)
        if device_ct not in self.location_type.content_types.all():
            self.location_type.content_types.add(device_ct)
        if hasattr(self.location_type, "nestable") and not self.location_type.nestable:
            self.location_type.nestable = True
            self.location_type.validated_save()

        user_secret = Secret.objects.get_or_create(
            name="ServiceNow 2026 Test User",
            defaults={"provider": "environment-variable", "parameters": {"variable": "NB_TEST_ENV_USER"}},
        )[0]
        pass_secret = Secret.objects.get_or_create(
            name="ServiceNow 2026 Test Password",
            defaults={"provider": "environment-variable", "parameters": {"variable": "NB_TEST_ENV_PASS"}},
        )[0]
        self.secrets_group = SecretsGroup.objects.get_or_create(name="ServiceNow 2026 Secrets")[0]
        SecretsGroupAssociation.objects.get_or_create(
            secret=user_secret,
            secrets_group=self.secrets_group,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
        )
        SecretsGroupAssociation.objects.get_or_create(
            secret=pass_secret,
            secrets_group=self.secrets_group,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
        )
        self.integration = ExternalIntegration.objects.get_or_create(
            name="ServiceNow 2026 Test",
            defaults={
                "remote_url": "https://example.service-now.com",
                "secrets_group": self.secrets_group,
                "verify_ssl": False,
            },
        )[0]
        if self.integration.secrets_group_id != self.secrets_group.id:
            self.integration.secrets_group = self.secrets_group
            self.integration.validated_save()

        for name in constants.SERVICENOW_METADATA_TYPES:
            MetadataType.objects.get_or_create(
                name=name,
                defaults={"data_type": MetadataTypeDataTypeChoices.TYPE_TEXT},
            )

    def _run_servicenow_to_nautobot(self, **kwargs):
        job = Job.objects.get(
            job_class_name="ServiceNowToNautobot",
            module_name="nautobot_ssot.integrations.servicenow2026.jobs",
        )
        return run_job_for_testing(
            job,
            servicenow_instance=self.integration.id,
            backend_choice=kwargs.get("backend_choice", "pysnc"),
            mapping_path=kwargs.get("mapping_path", str(constants.DEFAULT_MAPPING_PATH)),
            filter_mode=kwargs.get("filter_mode", "none"),
            root_location_sys_id=kwargs.get("root_location_sys_id", ""),
            location_types=kwargs.get("location_types", ""),
            include_unknown_type=kwargs.get("include_unknown_type", True),
            debug=kwargs.get("debug", True),
            dryrun=kwargs.get("dryrun", False),
            memory_profiling=kwargs.get("memory_profiling", False),
        )

    def _run_nautobot_to_servicenow(self, **kwargs):
        job = NautobotToServiceNow()
        job.job_result = JobResult.objects.create(
            name=job.class_path,
            task_name="servicenow2026",
            user=None,
        )
        job.run(
            dryrun=kwargs.get("dryrun", True),
            memory_profiling=kwargs.get("memory_profiling", False),
            integration=self.integration,
            backend=kwargs.get("backend", "pysnow"),
            mapping_path=kwargs.get("mapping_path", str(constants.DEFAULT_MAPPING_PATH)),
            delete_records=kwargs.get("delete_records", False),
            debug=kwargs.get("debug", True),
        )
        return job.job_result

    def test_servicenow_to_nautobot_job_with_filters(self):
        """ServiceNow → Nautobot job runs with filters and fixture data."""
        location_records = self.fixture_records[constants.SERVICENOW_TABLE_LOCATION]
        records_by_sys_id = {rec["sys_id"]: rec for rec in location_records if rec.get("sys_id")}
        parent_by_sys_id: Dict[str, Optional[str]] = {}
        children_map: Dict[str, List[str]] = defaultdict(list)
        for sys_id, record in records_by_sys_id.items():
            parent = record.get("parent")
            if isinstance(parent, dict):
                parent = parent.get("value") or parent.get("sys_id")
            elif parent is not None:
                parent = str(parent).strip() or None
            parent_by_sys_id[sys_id] = parent
            if parent:
                children_map[parent].append(sys_id)
        roots = [sys_id for sys_id, parent in parent_by_sys_id.items() if not parent]
        total_locations = len(records_by_sys_id)
        root_sys_id = roots[0]
        subtree_sys_ids: set[str] = set()
        for candidate in roots:
            candidate_subtree: set[str] = set()
            stack = [candidate]
            while stack:
                current = stack.pop()
                if current in candidate_subtree:
                    continue
                candidate_subtree.add(current)
                stack.extend(children_map.get(current, []))
            if 1 < len(candidate_subtree) < total_locations:
                root_sys_id = candidate
                subtree_sys_ids = candidate_subtree
                break
        if not subtree_sys_ids:
            stack = [root_sys_id]
            while stack:
                current = stack.pop()
                if current in subtree_sys_ids:
                    continue
                subtree_sys_ids.add(current)
                stack.extend(children_map.get(current, []))
        client_stub = FixtureServiceNowClient(self.fixture_records, self.integration)

        with patch(
            "nautobot_ssot.integrations.servicenow2026.jobs._ServiceNow2BaseJob._build_client",
            return_value=client_stub,
        ):
            job_result = self._run_servicenow_to_nautobot(
                backend_choice="pysnc",
                filter_mode="subtree+types",
                root_location_sys_id=root_sys_id,
                location_types="Demo",
                include_unknown_type=False,
            )

        self.assertEqual(job_result.status, JobResultStatusChoices.STATUS_SUCCESS)

        sys_id_type = MetadataType.objects.get(name=constants.SERVICENOW_METADATA_SYS_ID)
        location_ct = ContentType.objects.get_for_model(Location)
        location_sys_ids = set(
            ObjectMetadata.objects.filter(
                assigned_object_type=location_ct,
                metadata_type=sys_id_type,
            ).values_list("_value", flat=True)
        )
        self.assertSetEqual(location_sys_ids, subtree_sys_ids)

        company_records = self.fixture_records[constants.SERVICENOW_TABLE_COMPANY]
        company_by_sys_id = {rec["sys_id"]: rec for rec in company_records if rec.get("sys_id")}
        company_record = next(rec for rec in company_records if rec.get("name"))
        manufacturer_record = next(
            rec
            for rec in company_records
            if str(rec.get("manufacturer") or "").strip().lower() in {"true", "1", "yes", "y", "on"}
        )
        Tenant.objects.get(name=company_record["name"])
        Manufacturer.objects.get(name=manufacturer_record["name"])

        model_records = self.fixture_records[constants.SERVICENOW_TABLE_MODEL]
        model_record = next(
            rec
            for rec in model_records
            if rec.get("name") and rec.get("manufacturer") in company_by_sys_id
        )
        DeviceType.objects.get(
            model=model_record["name"],
            manufacturer__name=company_by_sys_id[model_record["manufacturer"]]["name"],
        )

        device_record = next(rec for rec in self.fixture_records[constants.SERVICENOW_TABLE_DEVICE] if rec.get("name"))
        device = Device.objects.get(name=device_record["name"])
        self.assertEqual(device.role, self.role_admin)
        self.assertEqual(device.status, self.status_active)
        location_sys_id = device_record.get("location")
        if isinstance(location_sys_id, dict):
            location_sys_id = location_sys_id.get("value") or location_sys_id.get("sys_id")
        elif location_sys_id is not None:
            location_sys_id = str(location_sys_id).strip() or None
        if location_sys_id in subtree_sys_ids:
            location_sys_id_value = ObjectMetadata.objects.filter(
                assigned_object_id=device.location_id,
                assigned_object_type=location_ct,
                metadata_type=sys_id_type,
            ).values_list("_value", flat=True).first()
            self.assertEqual(location_sys_id_value, location_sys_id)
        else:
            self.assertEqual(device.location.name, UNASSIGNED_LOCATION_NAME)

        self.assertIn({"manufacturer": True}, client_stub.queries[constants.SERVICENOW_TABLE_COMPANY])

    def test_nautobot_to_servicenow_job_delete_records_enabled(self):
        """Nautobot → ServiceNow job runs in dry-run mode with delete_records enabled."""
        source_client = FixtureServiceNowClient(self.fixture_records, self.integration)
        with patch(
            "nautobot_ssot.integrations.servicenow2026.jobs._ServiceNow2BaseJob._build_client",
            return_value=source_client,
        ):
            self._run_servicenow_to_nautobot(filter_mode="none")

        target_records = deepcopy(self.fixture_records)
        target_records[constants.SERVICENOW_TABLE_COMPANY].append(
            {"sys_id": "extra-company-1234", "name": "Extra Company"}
        )
        target_client = FixtureServiceNowClient(target_records, self.integration)
        with patch(
            "nautobot_ssot.integrations.servicenow2026.jobs._ServiceNow2BaseJob._build_client",
            return_value=target_client,
        ):
            job_result = self._run_nautobot_to_servicenow(delete_records=True)

        sync = Sync.objects.get(job_result=job_result)
        summary = sync.summary
        if isinstance(summary, str):
            try:
                summary = ast.literal_eval(summary)
            except (ValueError, SyntaxError):
                summary = {}
        self.assertGreater(summary.get("delete", 0), 0)

    def test_nautobot_to_servicenow_job_delete_records_disabled(self):
        """Nautobot → ServiceNow job runs in dry-run mode with delete_records disabled."""
        source_client = FixtureServiceNowClient(self.fixture_records, self.integration)
        with patch(
            "nautobot_ssot.integrations.servicenow2026.jobs._ServiceNow2BaseJob._build_client",
            return_value=source_client,
        ):
            self._run_servicenow_to_nautobot(filter_mode="none")

        target_records = deepcopy(self.fixture_records)
        target_records[constants.SERVICENOW_TABLE_COMPANY].append(
            {"sys_id": "extra-company-5678", "name": "Another Extra Company"}
        )
        target_client = FixtureServiceNowClient(target_records, self.integration)
        with patch(
            "nautobot_ssot.integrations.servicenow2026.jobs._ServiceNow2BaseJob._build_client",
            return_value=target_client,
        ):
            job_result = self._run_nautobot_to_servicenow(delete_records=False)

        sync = Sync.objects.get(job_result=job_result)
        summary = sync.summary
        if isinstance(summary, str):
            try:
                summary = ast.literal_eval(summary)
            except (ValueError, SyntaxError):
                summary = {}
        self.assertEqual(summary.get("delete", 0), 0)
