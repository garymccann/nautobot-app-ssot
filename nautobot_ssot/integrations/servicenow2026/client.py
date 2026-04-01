"""ServiceNow API client abstraction supporting PySNC and PySnow backends."""

from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

import requests
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import ExternalIntegration, SecretsGroupAssociation

from nautobot_ssot.integrations.servicenow2026.constants import DEFAULT_CLIENT_PAGE_SIZE, DEFAULT_CLIENT_TIMEOUT


class ServiceNowClientError(Exception):
    """Base exception for ServiceNow client errors."""


class ServiceNowBackendBase:
    """Base class for ServiceNow client backends."""

    def __init__(self, integration: ExternalIntegration, page_size: int = 0):
        """Initialize the backend with integration configuration.

        Args:
            integration: ExternalIntegration object.
            page_size: Optional page size for Pagination support.
        """
        self.integration: ExternalIntegration = integration
        if not integration.secrets_group:
            raise ServiceNowClientError("Secrets group not found on External Integration.")
        access_type = getattr(integration.secrets_group, "access_type", None)
        if access_type and access_type != SecretsGroupAccessTypeChoices.TYPE_HTTP:
            raise ServiceNowClientError("Secrets group access type must be HTTP(S).")
        self.token, self.username, self.password = self._get_credentials(self.integration)
        if not self.token and not (self.username and self.password):
            raise ServiceNowClientError("External Integration must provide token or username/password secrets.")
        self.page_size = page_size if page_size > 0 else DEFAULT_CLIENT_PAGE_SIZE

    @staticmethod
    def _get_credentials(integration: ExternalIntegration) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return token/username/password from the ExternalIntegration.

        Args:
            integration: ExternalIntegration-like object.

        Returns:
            Tuple of (token, username, password).
        """
        access_type = SecretsGroupAccessTypeChoices.TYPE_HTTP
        try:
            token: str|None = integration.secrets_group.get_secret_value(
                access_type=access_type, secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN
            )
        except SecretsGroupAssociation.DoesNotExist:
            token = None
        try:
            username: str|None = integration.secrets_group.get_secret_value(
                access_type=access_type, secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME
            )
            password: str|None = integration.secrets_group.get_secret_value(
                access_type=access_type, secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD
            )
        except SecretsGroupAssociation.DoesNotExist:
            username = None
            password = None
        return token, username, password

    @staticmethod
    def _coerce_record(record: Any) -> Dict[str, Any]:
        """Convert a backend record into a dictionary.

        Args:
            record: Backend record object.

        Returns:
            Record converted to a dictionary.
        """
        if isinstance(record, dict):
            return record
        if hasattr(record, "to_dict"):
            return record.to_dict()
        if hasattr(record, "raw"):
            return record.raw
        try:
            return dict(record)
        except TypeError:
            return {"value": record}

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        raise NotImplementedError("Backend must implement iter_table().")

    def _request_headers(self) -> Dict[str, str]:
        """Build default headers for ServiceNow REST requests."""
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request_kwargs(self) -> Dict[str, Any]:
        """Build kwargs for requests calls."""
        kwargs: Dict[str, Any] = {
            "headers": self._request_headers(),
            "verify": self.integration.verify_ssl,
            "timeout": DEFAULT_CLIENT_TIMEOUT,
        }
        if not self.token and self.username and self.password:
            kwargs["auth"] = (self.username, self.password)
        return kwargs

    def _table_url(self, table: str, sys_id: Optional[str] = None) -> str:
        """Build a ServiceNow Table API URL for the current instance."""
        base_url = (self.integration.remote_url or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}"
        url = f"{base_url}/api/now/table/{table}"
        if sys_id:
            return f"{url}/{sys_id}"
        return url

    def _request_result(self, method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a ServiceNow REST request and return the result dictionary."""
        kwargs = self._request_kwargs()
        if payload is not None:
            kwargs["json"] = payload
        response = requests.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise ServiceNowClientError(
                f"ServiceNow request failed ({response.status_code}) for {method} {url}: {response.text}"
            )
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
        except ValueError:
            return {}
        result = body.get("result")
        if isinstance(result, dict):
            return result
        return body if isinstance(body, dict) else {}

    def create_record(self, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a ServiceNow table record."""
        return self._request_result("POST", self._table_url(table), payload=payload)

    def update_record(self, table: str, sys_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update a ServiceNow table record by sys_id."""
        return self._request_result("PATCH", self._table_url(table, sys_id=sys_id), payload=payload)

    def delete_record(self, table: str, sys_id: str) -> None:
        """Delete a ServiceNow table record by sys_id."""
        self._request_result("DELETE", self._table_url(table, sys_id=sys_id))


class PySnowBackend(ServiceNowBackendBase):
    """PySnow-backed ServiceNow client."""

    def __init__(self, integration: ExternalIntegration, page_size: int):
        """Initialize PySnow backend.

        Args:
            integration: ExternalIntegration-like object.
        """
        super().__init__(integration, page_size)
        try:
            # Load PySNOW at runtime rather than import time to aid in removing this dependency
            # in future releases. PySNOW has not been maintained for many years resulting in bugs
            # and security vulnerabilities.
            from nautobot_ssot.integrations.servicenow.third_party.pysnow import Client as PySnowClient
        except ImportError as exc:
            raise ServiceNowClientError("PySnow is not installed or could not be imported.") from exc

        if self.token:
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {self.token}"})
            session.verify = self.integration.verify_ssl
            self.client = PySnowClient(
                instance=self.integration.remote_url, session=session, use_ssl=self.integration.verify_ssl
            )
        else:
            self.client = PySnowClient(
                instance=self.integration.remote_url,
                user=self.username,
                password=self.password,
                use_ssl=self.integration.verify_ssl,
            )

        self.client.parameters.exclude_reference_link = True

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table using PySnow streaming.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        query = query or {}
        resource = self.client.resource(api_path=f"/table/{table}")
        for record in resource.get(query=query, stream=True).all():
            yield self._coerce_record(record)

    def _request_headers(self) -> Dict[str, str]:
        """Build headers for PySnow-compatible token auth."""
        headers = super()._request_headers()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


class PySNCBackend(ServiceNowBackendBase):
    """PySNC-backed ServiceNow client."""

    def __init__(self, integration, page_size: int):
        """Initialize PySNC backend.

        Args:
            integration: ExternalIntegration-like object.
        """
        super().__init__(integration, page_size)
        try:
            from pysnc import ServiceNowClient as PySNCClient
        except ImportError as exc:
            raise ServiceNowClientError("PySNC is not installed or could not be imported.") from exc

        if self.token:
            session = requests.Session()
            session.headers.update({"x-sn-apikey": self.token})
            session.verify = self.integration.verify_ssl
            try:
                self.client = PySNCClient(instance=self.integration.remote_url, auth=session)
            except TypeError as exc:
                raise ServiceNowClientError(
                    "PySNC token authentication is not supported; use username/password or PySnow."
                ) from exc
        elif self.username and self.password:
            self.client = PySNCClient(
                instance=self.integration.remote_url,
                auth=(self.username, self.password),
                verify=self.integration.verify_ssl,
            )
        else:
            raise ServiceNowClientError("Username/password or token must be provided.")

    @staticmethod
    def _fetch_batch(table_client, query: Dict[str, Any], limit: int, offset: int) -> Iterable[Any]:
        """Fetch a single batch of records from a PySNC table client.

        Args:
            table_client: PySNC table client instance.
            query: Query dictionary to apply.
            limit: Page size.
            offset: Offset into the result set.

        Returns:
            Iterable of backend records.
        """
        if hasattr(table_client, "get"):
            try:
                return table_client.get(query=query, limit=limit, offset=offset)
            except TypeError:
                return table_client.get(query=query)
        if hasattr(table_client, "query"):
            return table_client.query(query=query)
        if hasattr(table_client, "all"):
            return table_client.all()
        return []

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table using PySNC.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        query = query or {}
        for method_name in ("table", "resource", "get_table"):
            method = getattr(self.client, method_name, None)
            if method:
                try:
                    table_client = method(table)
                except TypeError:
                    continue
        glide_record = getattr(self.client, "GlideRecord", None)
        if glide_record:
            try:
                table_client = glide_record(table)
            except TypeError:
                raise ServiceNowClientError("Unable to resolve PySNC table client.")

        if hasattr(table_client, "add_query") and hasattr(table_client, "query"):
            clear_query = getattr(table_client, "clear_query", None) or getattr(table_client, "_clear_query", None)
            if callable(clear_query):
                clear_query()
            for key, value in query.items():
                if key == "encoded_query":
                    table_client.add_encoded_query(value)
                else:
                    table_client.add_query(key, value)
            table_client.batch_size = self.page_size
            table_client.query()
            for record in table_client:
                yield self._coerce_record(record.serialize(exclude_reference_link=True))
            return

        offset = 0
        while True:
            batch = self._fetch_batch(table_client, query=query, limit=self.page_size, offset=offset)
            if not batch:
                return
            batch_counter:int = 0
            for record in batch:
                batch_counter += 1
                yield self._coerce_record(record)
            if batch_counter <= self.page_size:
                return
            offset += self.page_size

    def _request_headers(self) -> Dict[str, str]:
        """Build headers for PySNC-compatible token auth."""
        headers = super()._request_headers()
        if self.token:
            headers["x-sn-apikey"] = self.token
        return headers


class ServiceNowClient:
    """ServiceNow client wrapper with backend selection."""

    def __init__(self, integration: ExternalIntegration, backend: str, page_size: int = DEFAULT_CLIENT_PAGE_SIZE):
        """Initialize the client and select a backend.

        Args:
            integration: ExternalIntegration-like object.
            backend: Backend name (pysnc, pysnow).
        """
        self.integration: ExternalIntegration = integration
        self.page_size: int = page_size
        self.backend: ServiceNowBackendBase = self._select_backend(backend)

    def _select_backend(self, backend: str) -> ServiceNowBackendBase:
        """Select and initialize a backend by name.

        Args:
            backend: Backend name (pysnc, pysnow).

        Returns:
            Initialized backend instance.
        """
        if backend == "pysnc":
            return PySNCBackend(self.integration, self.page_size)
        elif backend == "pysnow":
            return PySnowBackend(self.integration, self.page_size)
        else:
            raise ServiceNowClientError(f"Unsupported backend '{backend}'.")

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table using the configured backend.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        return self.backend.iter_table(table=table, query=query)

    def create_record(self, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a ServiceNow table record via the configured backend."""
        return self.backend.create_record(table=table, payload=payload)

    def update_record(self, table: str, sys_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update a ServiceNow table record via the configured backend."""
        return self.backend.update_record(table=table, sys_id=sys_id, payload=payload)

    def delete_record(self, table: str, sys_id: str) -> None:
        """Delete a ServiceNow table record via the configured backend."""
        self.backend.delete_record(table=table, sys_id=sys_id)
