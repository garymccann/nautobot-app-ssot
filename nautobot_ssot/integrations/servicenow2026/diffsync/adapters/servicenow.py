"""ServiceNow DiffSync adapter for ServiceNow 2026."""

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Set

from diffsync import Adapter
from diffsync.exceptions import ObjectCrudException
from diffsync.exceptions import ObjectAlreadyExists
from django.contrib.contenttypes.models import ContentType
from nautobot.tenancy.models import Tenant
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata

from nautobot_ssot.integrations.servicenow2026.client import ServiceNowClient
from nautobot_ssot.integrations.servicenow2026.diffsync import models
from nautobot_ssot.integrations.servicenow2026.mapping import load_mapping, map_record
from nautobot_ssot.integrations.servicenow2026.utils import metadata as metadata_utils
from nautobot_ssot.integrations.servicenow2026.utils.metadata import build_servicenow_url
from nautobot_ssot.integrations.servicenow2026 import constants

import requests


class ServiceNowAdapter(Adapter):  # pylint: disable=too-many-instance-attributes
    """DiffSync adapter loading data from ServiceNow."""

    _duplicate_name_rules = {
        "company": {
            "name_field": "name",
            "key_fields": ("name",),
        },
        "manufacturer": {
            "name_field": "name",
            "key_fields": ("name",),
        },
        "platform": {
            "name_field": "name",
            "key_fields": ("name",),
        },
        "device_type": {
            "name_field": "model",
            "key_fields": ("manufacturer_sys_id", "model"),
            "allow_none_fields": ("manufacturer_sys_id",),
        },
        "location": {
            "name_field": "name",
            "key_fields": ("parent_sys_id", "name"),
            "allow_none_fields": ("parent_sys_id",),
        },
        "device": {
            "name_field": "name",
            "key_fields": ("name",),
        },
    }

    company = models.Company
    manufacturer = models.Manufacturer
    platform = models.Platform
    device_type = models.DeviceType
    location = models.Location
    device = models.Device

    top_level = (
        "company",
        "manufacturer",
        "platform",
        "device_type",
        "location",
        "device",
    )

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *args,
        client: ServiceNowClient,
        job=None,
        mapping_path=None,
        filter_mode: str = "none",
        location_types: Optional[List[str]] = None,
        include_unknown_type: bool = True,
        root_location_sys_id: Optional[str] = None,
        **kwargs,
    ):
        """Initialize the ServiceNow adapter.

        Args:
            client: ServiceNow client wrapper.
            job: Optional Nautobot Job instance for logging.
            mapping_path: Optional mapping file path.
            filter_mode: Location filter mode.
            location_types: List of location types to include.
            include_unknown_type: Whether to include locations with unknown type.
            root_location_sys_id: Root sys_id for subtree filtering.
        """
        super().__init__(*args, **kwargs)
        self.client = client
        self.job = job
        self.mapping_path = mapping_path
        self.filter_mode = filter_mode
        self.location_types = location_types or []
        self.include_unknown_type = include_unknown_type
        self.root_location_sys_id = root_location_sys_id
        self.mapping = {}
        self.loaded_sys_ids: Dict[str, Set[str]] = {}
        self._session: Optional[requests.Session] = None

    def load(self):
        """Load all ServiceNow data into DiffSync models."""
        self.mapping = load_mapping(self.mapping_path)
        for model_name, entry in self.mapping.items():
            self._load_table(model_name, entry)

    def _load_table(self, model_name: str, entry: Dict[str, Any]):
        """Load a single ServiceNow table into DiffSync models.

        Args:
            model_name: DiffSync model name.
            entry: Mapping entry for the model.
        """
        table = entry.get("table")
        if not table:
            return
        records = self._collect_records(model_name, entry, table)
        self._add_records(model_name, records)
        self._update_loaded_sys_ids(model_name, records)
        if self.job:
            self.job.logger.info("Loaded %s %s records from ServiceNow.", len(records), model_name)

    def _collect_records(self, model_name: str, entry: Dict[str, Any], table: str) -> List[Dict[str, Any]]:
        """Collect and normalize records for a ServiceNow table.

        Args:
            model_name: DiffSync model name.
            entry: Mapping entry for the model.
            table: ServiceNow table name.

        Returns:
            List of normalized record dictionaries.
        """
        mappings = entry.get("mappings", [])
        table_query = entry.get("table_query", {})
        raw_records = list(self._iter_records(table, table_query))
        records = [self._build_attributes(record, mappings, table, model_name) for record in raw_records]
        records = [record for record in records if record.get("sys_id")]
        records = self._order_records(model_name, records)
        records = self._apply_duplicate_name_suffixes(model_name, records)
        return self.null_unresolved_references(model_name, records)

    def _order_records(self, model_name: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Order records deterministically, handling location hierarchy.

        Args:
            model_name: DiffSync model name.
            records: Record dictionaries to order.

        Returns:
            Ordered list of record dictionaries.
        """
        if model_name == "location":
            records = self._filter_locations(records)
            return self._order_locations(records)
        return sorted(records, key=lambda item: item.get("sys_id") or "")

    def _add_records(self, model_name: str, records: List[Dict[str, Any]]) -> None:
        """Add records to the adapter.

        Args:
            model_name: DiffSync model name.
            records: Record dictionaries to add.
        """
        model_class = getattr(self, model_name)
        for record in records:
            model = model_class(**record)
            try:
                self.add(model)
            except ObjectAlreadyExists:
                if self.job:
                    self.job.logger.warning("Duplicate %s with sys_id %s skipped.", model_name, record.get("sys_id"))

    def _update_loaded_sys_ids(self, model_name: str, records: List[Dict[str, Any]]) -> None:
        """Track sys_ids loaded for reference validation.

        Args:
            model_name: DiffSync model name.
            records: Record dictionaries used for tracking.
        """
        self.loaded_sys_ids[model_name] = {
            record.get("servicenow_sys_id") or record.get("sys_id") for record in records if record.get("sys_id")
        }

    def create_servicenow_record(self, model_class, ids: Dict[str, Any], attrs: Dict[str, Any]) -> Optional[str]:
        """Create a ServiceNow record and return its sys_id."""
        entry = self.mapping.get(model_class._modelname, {})
        table = entry.get("table")
        if not table:
            raise ObjectCrudException(f"No ServiceNow table configured for {model_class._modelname}.")
        payload = self._build_payload(entry, ids, attrs)
        result = self._request("post", f"/api/now/table/{table}", payload)
        if not result:
            return None
        return result.get("sys_id")

    def update_servicenow_record(self, model_class, sys_id: Optional[str], attrs: Dict[str, Any]) -> None:
        """Update a ServiceNow record by sys_id."""
        if not sys_id:
            raise ObjectCrudException(f"Missing ServiceNow sys_id for {model_class._modelname} update.")
        entry = self.mapping.get(model_class._modelname, {})
        table = entry.get("table")
        if not table:
            raise ObjectCrudException(f"No ServiceNow table configured for {model_class._modelname}.")
        payload = self._build_payload(entry, {}, attrs)
        if not payload:
            return
        self._request("patch", f"/api/now/table/{table}/{sys_id}", payload)

    def delete_servicenow_record(self, model_class, sys_id: Optional[str]) -> None:
        """Delete a ServiceNow record by sys_id."""
        if not sys_id:
            raise ObjectCrudException(f"Missing ServiceNow sys_id for {model_class._modelname} delete.")
        entry = self.mapping.get(model_class._modelname, {})
        table = entry.get("table")
        if not table:
            raise ObjectCrudException(f"No ServiceNow table configured for {model_class._modelname}.")
        self._request("delete", f"/api/now/table/{table}/{sys_id}", None)

    def backfill_servicenow_sys_id(self, model_class, identifier_sys_id: Optional[str], sys_id: str) -> None:
        """Store ServiceNow sys_id metadata for a Nautobot object after create."""
        if not identifier_sys_id or not str(identifier_sys_id).startswith("nautobot:"):
            return
        pk = str(identifier_sys_id).split(":", 1)[1]
        obj = model_class._model.objects.filter(pk=pk).first()
        if not obj:
            if self.job:
                self.job.logger.warning("Unable to backfill ServiceNow sys_id for %s with pk %s.", model_class._modelname, pk)
            return
        entry = self.mapping.get(model_class._modelname, {})
        table = entry.get("table")
        instance = self.client.integration.remote_url.rstrip("/")
        self._set_object_metadata(obj, constants.SERVICENOW_METADATA_SYS_ID, sys_id)
        if table:
            self._set_object_metadata(obj, constants.SERVICENOW_METADATA_TABLE, table)
            self._set_object_metadata(obj, constants.SERVICENOW_METADATA_INSTANCE, instance)
            url = build_servicenow_url(instance=instance, table=table, sys_id=sys_id)
            if url:
                self._set_object_metadata(obj, constants.SERVICENOW_METADATA_URL, url)

    def _set_object_metadata(self, obj, metadata_key: str, value: str) -> None:
        """Set ObjectMetadata value on a Nautobot object."""
        metadata_type = MetadataType.objects.filter(name=metadata_key).first()
        if not metadata_type:
            return
        content_type = ContentType.objects.get_for_model(type(obj))
        if content_type not in metadata_type.content_types.all():
            metadata_type.content_types.add(content_type)
        metadata, created = ObjectMetadata.objects.get_or_create(
            assigned_object_id=obj.id,
            assigned_object_type=content_type,
            metadata_type=metadata_type,
            defaults={"_value": value, "scoped_fields": []},
        )
        if not created:
            if metadata.scoped_fields is None:
                metadata.scoped_fields = []
            metadata._value = value
            metadata.validated_save()

    def _build_payload(self, entry: Dict[str, Any], ids: Dict[str, Any], attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Build a ServiceNow API payload from identifiers and attributes."""
        data = {}
        data.update(ids)
        data.update(attrs)
        payload: Dict[str, Any] = {}
        for mapping in entry.get("mappings", []):
            field_name = mapping.get("field")
            if not field_name or field_name not in data:
                continue
            value = data[field_name]
            if value is None or value == "":
                continue
            if field_name == "servicenow_sys_id":
                continue
            if "column" in mapping:
                payload[mapping["column"]] = str(value) if isinstance(value, Decimal) else value
                continue
            if "reference" in mapping:
                reference_key = mapping["reference"].get("key")
                if reference_key:
                    payload[reference_key] = str(value) if isinstance(value, Decimal) else value
        return payload

    def _get_session(self) -> requests.Session:
        """Return a requests Session configured with ServiceNow auth."""
        if self._session:
            return self._session
        session = requests.Session()
        token = getattr(self.client.backend, "token", None)
        username = getattr(self.client.backend, "username", None)
        password = getattr(self.client.backend, "password", None)
        if token:
            session.headers.update({"Authorization": f"Bearer {token}", "x-sn-apikey": token})
        elif username and password:
            session.auth = (username, password)
        session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        session.verify = self.client.integration.verify_ssl
        self._session = session
        return session

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Perform a ServiceNow REST API request and return the result object."""
        base_url = self.client.integration.remote_url.rstrip("/")
        url = f"{base_url}{path}"
        session = self._get_session()
        response = session.request(method, url, json=payload)
        if not response.ok:
            raise ObjectCrudException(f"ServiceNow API {method.upper()} {url} failed: {response.status_code} {response.text}")
        if response.status_code == 204:
            return None
        data = response.json()
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data if isinstance(data, dict) else None

    def null_unresolved_references(self, model_name: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Clear reference sys_ids that don't exist in loaded datasets."""
        reference_map = {
            "location": {"tenant__sys_id": "company"},
            "device_type": {"manufacturer_sys_id": "manufacturer"},
            "platform": {"manufacturer_sys_id": "manufacturer"},
            "device": {
                "location_sys_id": "location",
                "device_type_sys_id": "device_type",
                "platform_sys_id": "platform",
            },
        }
        references = reference_map.get(model_name, {})
        if not references:
            return records

        for record in records:
            for field, related_model in references.items():
                value = record.get(field)
                if not value:
                    record[field] = None
                    continue
                loaded = self.loaded_sys_ids.get(related_model)
                if loaded is not None and value not in loaded:
                    record[field] = None
                    continue
                if model_name == "location" and field == "tenant__sys_id":
                    if metadata_utils.get_object_by_sys_id(Tenant, value) is None:
                        record[field] = None
        return records

    def _apply_duplicate_name_suffixes(self, model_name: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Append sys_id suffixes when duplicate names exist in the dataset.

        Args:
            model_name: DiffSync model name.
            records: List of record attribute dictionaries.

        Returns:
            Records with name fields updated for duplicate groups.
        """
        rule = self._duplicate_name_rules.get(model_name)
        if not rule:
            return records
        groups = self._group_duplicate_records(records, rule)
        self._apply_suffixes_to_groups(groups, rule["name_field"])
        return records

    def _group_duplicate_records(
        self, records: List[Dict[str, Any]], rule: Dict[str, Any]
    ) -> Dict[tuple, List[Dict[str, Any]]]:
        """Group records by duplicate key rules.

        Args:
            records: List of record attribute dictionaries.
            rule: Duplicate rule configuration.

        Returns:
            Dictionary mapping duplicate keys to record lists.
        """
        name_field = rule["name_field"]
        key_fields = rule["key_fields"]
        allow_none_fields = set(rule.get("allow_none_fields", ()))
        groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
        for record in records:
            key = self._build_duplicate_key(record, name_field, key_fields, allow_none_fields)
            if key is None:
                continue
            groups[key].append(record)
        return groups

    @staticmethod
    def _build_duplicate_key(
        record: Dict[str, Any],
        name_field: str,
        key_fields: Iterable[str],
        allow_none_fields: Set[str],
    ) -> Optional[tuple]:
        """Return a duplicate grouping key for a record.

        Args:
            record: Record attribute dictionary.
            name_field: Field containing the display name.
            key_fields: Fields that define uniqueness.
            allow_none_fields: Fields that may be None in a key.

        Returns:
            Tuple key when valid, otherwise None.
        """
        base_name = record.get(name_field)
        if not base_name:
            return None
        key_values = []
        for field in key_fields:
            value = record.get(field)
            if value is None and field not in allow_none_fields:
                return None
            key_values.append(value)
        return tuple(key_values)

    def _apply_suffixes_to_groups(self, groups: Dict[tuple, List[Dict[str, Any]]], name_field: str) -> None:
        """Apply name suffixes for duplicate groups.

        Args:
            groups: Grouped record dictionary.
            name_field: Field containing the display name.
        """
        for group in groups.values():
            if len(group) <= 1:
                continue
            for record in group:
                self._apply_suffix_to_record(record, name_field)

    def _apply_suffix_to_record(self, record: Dict[str, Any], name_field: str) -> None:
        """Apply sys_id suffix to a single record name if needed.

        Args:
            record: Record attribute dictionary.
            name_field: Field containing the display name.
        """
        base_name = record.get(name_field)
        sys_id = record.get("sys_id") or record.get("servicenow_sys_id")
        if not base_name or not sys_id:
            return
        suffix = f" ({str(sys_id)[:8]})"
        if base_name.endswith(suffix):
            return
        base_name = self.strip_suffix(base_name)
        record[name_field] = f"{base_name}{suffix}"

    @staticmethod
    def strip_suffix(name: str) -> str:
        """Remove an existing 8-hex suffix when present."""
        if not name.endswith(")") or " (" not in name:
            return name
        prefix, suffix = name.rsplit(" (", 1)
        suffix = suffix.rstrip(")")
        if len(suffix) == 8 and all(char in "0123456789abcdefABCDEF" for char in suffix):
            return prefix
        return name

    def _iter_records(self, table: str, query: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        """Iterate over ServiceNow records for a table.

        Args:
            table: ServiceNow table name.
            query: Query dictionary.

        Returns:
            Iterable of ServiceNow records.
        """
        yield from self.client.iter_table(table=table, query=query)

    def _build_attributes(
        self,
        record: Dict[str, Any],
        mappings: List[Dict[str, Any]],
        table: str,
        model_name: str,
    ) -> Dict[str, Any]:
        """Build DiffSync attributes for a ServiceNow record.

        Args:
            record: ServiceNow record dictionary.
            mappings: Mapping entries for the model.
            table: ServiceNow table name.
            model_name: DiffSync model name.

        Returns:
            Dictionary of DiffSync attributes.
        """
        attributes = map_record(record, mappings)
        defaults = self.mapping.get(model_name, {}).get("defaults", {})
        self.apply_defaults(attributes, defaults)
        attributes["sys_id"] = record.get("sys_id")
        attributes["servicenow_sys_id"] = record.get("sys_id")
        attributes["servicenow_table"] = table
        attributes["servicenow_instance"] = self.client.integration.remote_url
        attributes["servicenow_url"] = build_servicenow_url(
            instance=self.client.integration.remote_url,
            table=table,
            sys_id=record.get("sys_id"),
        )
        return attributes

    @staticmethod
    def apply_defaults(attributes: Dict[str, Any], defaults: Dict[str, Any]) -> None:
        """Apply default values when attributes are missing or empty."""
        for field, value in defaults.items():
            if value is None or value == "":
                continue
            if field not in attributes or attributes.get(field) in (None, ""):
                attributes[field] = value

    def _filter_locations(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply location filters and return filtered records.

        Args:
            records: List of location attribute dictionaries.

        Returns:
            Filtered list of location attributes.
        """
        if self.filter_mode == "none":
            return records

        records_by_sys_id = {record["sys_id"]: record for record in records}
        allowed_sys_ids: Set[str] = set(records_by_sys_id)

        if "subtree" in self.filter_mode and self.root_location_sys_id:
            allowed_sys_ids = self._collect_subtree_sys_ids(records_by_sys_id, self.root_location_sys_id)

        if "types" in self.filter_mode and self.location_types:
            filtered = set()
            for sys_id in allowed_sys_ids:
                location_type = records_by_sys_id[sys_id].get("location_type__name")
                if location_type in self.location_types:
                    filtered.add(sys_id)
                elif location_type is None and self.include_unknown_type:
                    filtered.add(sys_id)
            allowed_sys_ids = filtered

        allowed_sys_ids = self._include_ancestors(records_by_sys_id, allowed_sys_ids)
        return [records_by_sys_id[sys_id] for sys_id in allowed_sys_ids]

    @staticmethod
    def _collect_subtree_sys_ids(records_by_sys_id: Dict[str, Dict[str, Any]], root_sys_id: str) -> Set[str]:
        """Collect sys_ids for all descendants of a root sys_id.

        Args:
            records_by_sys_id: Mapping of sys_id to record dictionary.
            root_sys_id: Root sys_id to start traversal.

        Returns:
            Set of sys_ids in the subtree.
        """
        children_map: Dict[str, List[str]] = defaultdict(list)
        for sys_id, record in records_by_sys_id.items():
            parent_sys_id = record.get("parent_sys_id")
            if parent_sys_id:
                children_map[parent_sys_id].append(sys_id)

        collected = set()
        stack = [root_sys_id]
        while stack:
            current = stack.pop()
            if current in collected:
                continue
            collected.add(current)
            stack.extend(children_map.get(current, []))
        return collected

    @staticmethod
    def _include_ancestors(records_by_sys_id: Dict[str, Dict[str, Any]], allowed_sys_ids: Set[str]) -> Set[str]:
        """Ensure all ancestors of allowed sys_ids are included.

        Args:
            records_by_sys_id: Mapping of sys_id to record dictionary.
            allowed_sys_ids: Set of allowed sys_ids.

        Returns:
            Updated set of allowed sys_ids with ancestors included.
        """
        for sys_id in list(allowed_sys_ids):
            parent_sys_id = records_by_sys_id[sys_id].get("parent_sys_id")
            while parent_sys_id and parent_sys_id in records_by_sys_id:
                if parent_sys_id in allowed_sys_ids:
                    break
                allowed_sys_ids.add(parent_sys_id)
                parent_sys_id = records_by_sys_id[parent_sys_id].get("parent_sys_id")
        return allowed_sys_ids

    @staticmethod
    def _order_locations(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Order location records with parents before children.

        Args:
            records: List of location attribute dictionaries.

        Returns:
            Ordered list of location attributes.
        """
        records_by_sys_id = {record["sys_id"]: record for record in records}
        ordered: List[Dict[str, Any]] = []
        visited: Set[str] = set()

        def visit(record: Dict[str, Any]):
            sys_id = record.get("sys_id")
            if not sys_id or sys_id in visited:
                return
            parent_sys_id = record.get("parent_sys_id")
            if parent_sys_id and parent_sys_id in records_by_sys_id:
                visit(records_by_sys_id[parent_sys_id])
            visited.add(sys_id)
            ordered.append(record)

        for record in sorted(records, key=lambda item: item.get("sys_id") or ""):
            visit(record)
        return ordered
