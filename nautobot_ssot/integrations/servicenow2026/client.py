"""ServiceNow API client abstraction supporting PySNC and PySnow backends."""

import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Optional
from urllib.parse import urlparse

import requests

from nautobot_ssot.integrations.servicenow.third_party.pysnow import Client as PySnowClient

logger = logging.getLogger("nautobot.ssot")


class ServiceNowClientError(Exception):
    """Base exception for ServiceNow client errors."""


class ServiceNowBackendUnavailable(ServiceNowClientError):
    """Raised when a requested backend is unavailable."""


class ServiceNowBackendConfigError(ServiceNowClientError):
    """Raised when backend configuration is invalid."""


@dataclass
class ServiceNowConfig:  # pylint: disable=too-many-instance-attributes
    """Configuration for ServiceNow client connections."""

    instance: Optional[str] = None
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    verify_ssl: bool = True
    timeout: int = 30
    page_size: int = 1000

    def resolve_instance(self) -> Optional[str]:
        """Return a normalized instance name if possible.

        Returns:
            Instance name if resolvable, otherwise None.
        """
        if self.instance:
            return self.instance
        if not self.base_url:
            return None
        parsed = urlparse(self.base_url)
        host = parsed.netloc or parsed.path
        if not host:
            return None
        return host.split(".")[0]

    def resolve_base_url(self) -> Optional[str]:
        """Return a normalized base URL for the ServiceNow instance.

        Returns:
            Base URL if resolvable, otherwise None.
        """
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.instance:
            return f"https://{self.instance}.service-now.com"
        return None

    def resolve_host(self) -> Optional[str]:
        """Return a normalized host for PySnow configuration.

        Returns:
            Hostname if resolvable, otherwise None.
        """
        if not self.base_url:
            return None
        parsed = urlparse(self.base_url)
        host = parsed.netloc or parsed.path
        return host or None


class ServiceNowBackendBase:
    """Base class for ServiceNow client backends."""

    def __init__(self, config: ServiceNowConfig):
        """Initialize the backend with configuration.

        Args:
            config: ServiceNow connection configuration.
        """
        self.config = config

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        raise NotImplementedError("Backend must implement iter_table().")


class PySnowBackend(ServiceNowBackendBase):
    """PySnow-backed ServiceNow client."""

    def __init__(self, config: ServiceNowConfig):
        """Initialize PySnow backend.

        Args:
            config: ServiceNow connection configuration.
        """
        super().__init__(config)
        host = config.resolve_host()
        instance = config.resolve_instance()
        if host:
            instance = None
        if not (host or instance):
            raise ServiceNowBackendConfigError("ServiceNow host or instance must be provided.")
        use_ssl = True
        if config.base_url:
            parsed = urlparse(config.base_url)
            if parsed.scheme:
                use_ssl = parsed.scheme != "http"
        if config.token:
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {config.token}"})
            session.verify = config.verify_ssl
            self.client = PySnowClient(host=host, instance=instance, session=session, use_ssl=use_ssl)
        else:
            if not (config.username and config.password):
                raise ServiceNowBackendConfigError("Username/password or token must be provided.")
            self.client = PySnowClient(
                host=host,
                instance=instance,
                user=config.username,
                password=config.password,
                use_ssl=use_ssl,
            )

        self.client.parameters.exclude_reference_link = True
        if self.client.session:
            self.client.session.verify = config.verify_ssl

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
            yield _coerce_record(record)


class PySNCBackend(ServiceNowBackendBase):
    """PySNC-backed ServiceNow client."""

    def __init__(self, config: ServiceNowConfig):
        """Initialize PySNC backend.

        Args:
            config: ServiceNow connection configuration.
        """
        super().__init__(config)
        client_class = _resolve_pysnc_client_class()
        if not client_class:
            raise ServiceNowBackendUnavailable("PySNC is not installed or could not be imported.")
        self.client = _initialize_pysnc_client(client_class, config)

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table using PySNC.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        query = query or {}
        table_client = _get_pysnc_table_client(self.client, table)
        if table_client is None:
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
            table_client.batch_size = self.config.page_size
            table_client.query()
            for record in table_client:
                yield _coerce_record(record.serialize(exclude_reference_link=True))
            return

        offset = 0
        while True:
            batch = _pysnc_fetch_batch(table_client, query=query, limit=self.config.page_size, offset=offset)
            if not batch:
                return
            for record in batch:
                yield _coerce_record(record)
            if len(batch) < self.config.page_size:
                return
            offset += self.config.page_size


class ServiceNowClient:
    """ServiceNow client wrapper with backend selection."""

    def __init__(self, config: ServiceNowConfig, backend: str = "auto"):
        """Initialize the client and select a backend.

        Args:
            config: ServiceNow connection configuration.
            backend: Backend name (auto, pysnc, pysnow).
        """
        self.config = config
        self.backend_name = backend
        self.backend = self._select_backend(backend)

    def _select_backend(self, backend: str) -> ServiceNowBackendBase:
        """Select and initialize a backend by name.

        Args:
            backend: Backend name (auto, pysnc, pysnow).

        Returns:
            Initialized backend instance.
        """
        if backend not in ("auto", "pysnc", "pysnow"):
            raise ServiceNowBackendConfigError(f"Unsupported backend '{backend}'.")
        if backend in ("auto", "pysnc"):
            try:
                return PySNCBackend(self.config)
            except ServiceNowBackendUnavailable:
                if backend == "pysnc":
                    raise
                logger.warning("PySNC unavailable; falling back to PySnow backend.")
        return PySnowBackend(self.config)

    def iter_table(self, table: str, query: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Iterate over records for a table using the configured backend.

        Args:
            table: ServiceNow table name.
            query: Optional query dictionary.

        Returns:
            Iterator of ServiceNow record dictionaries.
        """
        return self.backend.iter_table(table=table, query=query)

    def get_instance_name(self) -> Optional[str]:
        """Return the ServiceNow instance name for URL construction.

        Returns:
            Instance name if resolvable, otherwise None.
        """
        return self.config.resolve_instance()

    def get_base_url(self) -> Optional[str]:
        """Return the base URL for URL construction.

        Returns:
            Base URL if resolvable, otherwise None.
        """
        return self.config.resolve_base_url()


def _resolve_pysnc_client_class():
    """Return the PySNC client class if available.

    Returns:
        Client class if found, otherwise None.
    """
    candidates = (
        ("pysnc", "ServiceNowClient"),
        ("pysnc.client", "ServiceNowClient"),
        ("pysnc.client", "Client"),
    )
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        client_class = getattr(module, class_name, None)
        if client_class:
            return client_class
    return None


def _initialize_pysnc_client(client_class, config: ServiceNowConfig):
    """Initialize a PySNC client using available constructor parameters.

    Args:
        client_class: PySNC client class to instantiate.
        config: ServiceNow connection configuration.

    Returns:
        Initialized PySNC client instance.
    """
    params = inspect.signature(client_class).parameters
    auth_kwargs = _build_pysnc_auth_kwargs(params, config)
    if auth_kwargs:
        return client_class(**auth_kwargs)

    kwargs = _build_pysnc_basic_kwargs(params, config)
    if not kwargs:
        raise ServiceNowBackendConfigError("Unable to determine PySNC client parameters.")
    return client_class(**kwargs)


def _build_pysnc_auth_kwargs(params: Dict[str, Any], config: ServiceNowConfig) -> Dict[str, Any]:
    """Return kwargs for PySNC auth-based clients.

    Args:
        params: Client constructor parameters.
        config: ServiceNow connection configuration.

    Returns:
        Keyword arguments for PySNC auth initialization.
    """
    if "auth" not in params or "instance" not in params:
        return {}
    instance_value = config.resolve_base_url() or config.resolve_instance()
    if not instance_value:
        raise ServiceNowBackendConfigError("ServiceNow instance or base URL is required for PySNC.")
    auth_value = _build_pysnc_auth_value(config)
    kwargs: Dict[str, Any] = {"instance": instance_value, "auth": auth_value}
    if "verify" in params:
        kwargs["verify"] = config.verify_ssl
    return kwargs


def _build_pysnc_auth_value(config: ServiceNowConfig) -> Any:
    """Return the auth value for PySNC.

    Args:
        config: ServiceNow connection configuration.

    Returns:
        Auth value (session or credential tuple).
    """
    if config.token:
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {config.token}"})
        session.verify = config.verify_ssl
        return session
    if config.username and config.password:
        return (config.username, config.password)
    raise ServiceNowBackendConfigError("PySNC requires username/password or token.")


def _build_pysnc_basic_kwargs(params: Dict[str, Any], config: ServiceNowConfig) -> Dict[str, Any]:
    """Return kwargs for standard PySNC clients.

    Args:
        params: Client constructor parameters.
        config: ServiceNow connection configuration.

    Returns:
        Keyword arguments for PySNC initialization.
    """
    kwargs: Dict[str, Any] = {}
    options = (
        ("instance", config.resolve_instance()),
        ("base_url", config.resolve_base_url()),
        ("host", config.resolve_host()),
        ("user", config.username),
        ("username", config.username),
        ("password", config.password),
    )
    for key, value in options:
        if key in params and value:
            kwargs[key] = value
    if config.token:
        if "token" in params:
            kwargs["token"] = config.token
        elif "api_token" in params:
            kwargs["api_token"] = config.token
    if "verify_ssl" in params:
        kwargs["verify_ssl"] = config.verify_ssl
    if "timeout" in params:
        kwargs["timeout"] = config.timeout
    return kwargs


def _get_pysnc_table_client(client, table: str):
    """Return a PySNC table client for a table name.

    Args:
        client: PySNC client instance.
        table: ServiceNow table name.

    Returns:
        Table client instance if found, otherwise None.
    """
    for method_name in ("table", "resource", "get_table"):
        method = getattr(client, method_name, None)
        if method:
            try:
                return method(table)
            except TypeError:
                continue
    glide_record = getattr(client, "GlideRecord", None)
    if glide_record:
        try:
            return glide_record(table)
        except TypeError:
            return None
    return None


def _pysnc_fetch_batch(table_client, query: Dict[str, Any], limit: int, offset: int) -> Iterable[Any]:
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
