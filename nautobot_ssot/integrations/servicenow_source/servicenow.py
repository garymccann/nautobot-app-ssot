"""Simplified ServiceNow client using pysnc."""

from typing import Any, Dict, Iterable

import pysnc


class ServiceNowClient:
    """Wrapper around pysnc.Client with convenience helpers."""

    def __init__(self, instance: str, username: str, password: str):
        self.client = pysnc.ServiceNowClient(instance=instance, user=username, password=password)

    def all_table_entries(self, table: str, query: Dict[str, Any] | None = None) -> Iterable[Dict[str, Any]]:
        """Yield all records from a table."""
        resource = self.client.resource(api_path=f"/table/{table}")
        yield from resource.get(query=query or {}, stream=True).all()
