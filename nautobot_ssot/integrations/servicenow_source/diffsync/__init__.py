"""Dynamic DiffSync adapters for ServiceNow inbound integration."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict

import yaml
from diffsync import DiffSync
from pydantic import create_model

from nautobot_ssot.contrib.adapter import NautobotAdapter
from nautobot_ssot.contrib.model import NautobotModel


def create_model_class(name: str, model_path: str, identifiers: list[str], field_map: Dict[str, str]):
    """Create a DiffSync model class dynamically."""
    module_name, class_name = model_path.split(".", maxsplit=1)
    orm_module = import_module(module_name)
    orm_model = getattr(orm_module, class_name)

    fields: Dict[str, tuple[type, None]] = {}
    for field in identifiers:
        fields[field] = (str, None)
    for _, nb_field in field_map.items():
        if nb_field not in fields:
            fields[nb_field] = (str, None)

    model_cls = create_model(name, **fields, __base__=NautobotModel)
    model_cls._model = orm_model
    model_cls._identifiers = tuple(identifiers)
    model_cls._attributes = tuple(nb_field for nb_field in field_map.values())
    return model_cls


class ServiceNowDiffSync(DiffSync):
    """DiffSync adapter using dynamic models loaded from mapping file."""

    def __init__(self, mapping: Dict[str, Any], client=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mapping = mapping
        self.client = client
        self.models = {}
        self._generate_models()
        self.top_level = list(self.models.keys())

    def load(self):
        """Load data from ServiceNow using the provided client."""
        if self.client is None:
            return

        for table, table_map in self.mapping.items():
            model_cls = self.models[table]
            identifiers = table_map.get("identifiers", [])
            attr_map = table_map.get("attributes", {})
            for record in self.client.all_table_entries(table):
                ids = {field: record.get(field) for field in identifiers}
                attrs = {
                    nb_field: record.get(sn_field)
                    for sn_field, nb_field in attr_map.items()
                    if sn_field in record
                }
                model = model_cls(**ids, **attrs)
                self.add(model)

    def _generate_models(self):
        for table, table_map in self.mapping.items():
            nb_model_path = table_map["model"]
            identifiers = table_map.get("identifiers", [])
            attr_map = table_map.get("attributes", {})
            model_cls = create_model_class(table, nb_model_path, identifiers, attr_map)
            self.models[table] = model_cls
            setattr(self, table, model_cls)


class NautobotDynamicAdapter(NautobotAdapter):
    """Nautobot adapter loading models dynamically from mapping."""

    def __init__(self, mapping: Dict[str, Any], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mapping = mapping
        self._generate_models()

    def _generate_models(self):
        for table, table_map in self.mapping.items():
            nb_model_path = table_map["model"]
            identifiers = table_map.get("identifiers", [])
            attr_map = table_map.get("attributes", {})
            model_cls = create_model_class(table, nb_model_path, identifiers, attr_map)
            self.register_model(model_cls)


def load_mapping(path) -> Dict[str, Any]:
    """Load mapping file in YAML format."""
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)

