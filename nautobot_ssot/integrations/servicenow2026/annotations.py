"""ServiceNow 2026 type annotations."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ObjectMetadataAnnotation:
    """Map a model field to an ObjectMetadata type name.

    For usage with `typing.Annotated`.

    This allows DiffSync models to read and write ObjectMetadata values as part
    of the normal attribute sync process for the ServiceNow 2026 integration.

    Example:
        Given a metadata type named "ServiceNow Sys ID" on the Device model:

        ```python
        class DeviceModel(ServiceNowNautobotModel):
            _model: Device
            _identifiers = ("name",)
            _attributes = ("servicenow_sys_id",)

            servicenow_sys_id: Annotated[str, ObjectMetadataAnnotation(key="ServiceNow Sys ID")]
        ```
    """

    # TODO: Delete on 3.0, keep around for backwards compatibility for now
    name: Optional[str] = None
    key: Optional[str] = None

    def __post_init__(self):
        """Compatibility layer with using 'name' instead of 'key'.

        If `self.key` isn't set, fall back to the old behaviour.
        """
        if not self.key:
            if self.name:
                self.key = self.name
            else:
                raise ValueError("The 'key' field on ObjectMetadataAnnotation needs to be set.")
