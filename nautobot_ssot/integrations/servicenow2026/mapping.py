"""Mapping loader and helpers for ServiceNow 2026."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import yaml
from jinja2 import Environment, FileSystemLoader

from nautobot_ssot.integrations.servicenow2026 import constants


def load_mapping(mapping_path: Optional[Path] = None, template_context: Optional[Dict[str, Any]] = None) -> Dict:
    """Load a mapping file from disk.

    Args:
        mapping_path: Optional path to the mapping file.
        template_context: Optional context for Jinja2 templating.

    Returns:
        Mapping dictionary.

    Raises:
        FileNotFoundError: If the mapping file does not exist.
    """
    mapping_path = mapping_path or constants.DEFAULT_MAPPING_PATH
    if not mapping_path.is_file():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")
    template_context = template_context or {}
    env = Environment(loader=FileSystemLoader(str(mapping_path.parent)), autoescape=True)
    template = env.get_template(mapping_path.name)
    populated = template.render(template_context)
    return yaml.safe_load(populated) or {}


TransformSpec = Union[str, List[str], Dict[str, Any]]


def map_record(record: Dict[str, Any], mappings: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Map a ServiceNow record into DiffSync attributes.

    Args:
        record: ServiceNow record dictionary.
        mappings: Iterable of mapping configuration entries.

    Returns:
        Dictionary of mapped attributes.
    """
    attributes: Dict[str, Any] = {}
    for mapping in mappings:
        field_name = mapping.get("field")
        if not field_name:
            continue
        if "column" in mapping:
            value = record.get(mapping["column"])
            attributes[field_name] = _apply_transform(value, mapping.get("transform"))
            continue
        if "reference" in mapping:
            reference_key = mapping["reference"].get("key")
            value = _extract_reference_value(record.get(reference_key))
            attributes[field_name] = _apply_transform(value, mapping.get("transform"))
    return attributes


def _extract_reference_value(value: Any) -> Optional[str]:
    """Extract a reference sys_id from a ServiceNow field value.

    Args:
        value: ServiceNow reference value.

    Returns:
        sys_id string if available, otherwise None.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value") or value.get("sys_id")
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        return value
    return str(value)


def _apply_transform(value: Any, transform: Optional[TransformSpec]) -> Any:
    """Apply one or more transforms to a value.

    Args:
        value: Value to transform.
        transform: Transform name, list of names, or mapping with name key.

    Returns:
        Transformed value.
    """
    if transform is None:
        return value
    transforms = _normalize_transforms(transform)
    for name in transforms:
        transform_fn = _TRANSFORMS.get(name)
        if transform_fn:
            value = transform_fn(value)
    return value


def _normalize_transforms(transform: TransformSpec) -> List[str]:
    """Normalize transform specifications into a list of names.

    Args:
        transform: Transform name, list, or mapping.

    Returns:
        List of transform names.
    """
    if isinstance(transform, list):
        return [str(item) for item in transform]
    if isinstance(transform, dict):
        name = transform.get("name")
        return [str(name)] if name else []
    return [str(transform)]


def _to_string(value: Any) -> Optional[str]:
    """Convert value to string if possible.

    Args:
        value: Value to convert.

    Returns:
        String value or None.
    """
    if value is None:
        return None
    return str(value)


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Convert value to Decimal using default precision.

    Args:
        value: Value to convert.

    Returns:
        Decimal value or None.
    """
    if value in (None, ""):
        return None
    return _format_decimal(value)


def _strip(value: Any) -> Any:
    """Strip whitespace from strings.

    Args:
        value: Value to normalize.

    Returns:
        Normalized value.
    """
    if isinstance(value, str):
        return value.strip()
    return value


def _lower(value: Any) -> Any:
    """Lowercase string values.

    Args:
        value: Value to normalize.

    Returns:
        Normalized value.
    """
    if isinstance(value, str):
        return value.lower()
    return value


def _upper(value: Any) -> Any:
    """Uppercase string values.

    Args:
        value: Value to normalize.

    Returns:
        Normalized value.
    """
    if isinstance(value, str):
        return value.upper()
    return value


def _format_decimal(value: Any, max_digits: Optional[int] = None, decimal_places: int = 6) -> Optional[Decimal]:
    """Normalize numeric values to a fixed precision and enforce max digits.

    Args:
        value: Input value to normalize.
        max_digits: Maximum digits allowed (excluding sign and decimal separator).
        decimal_places: Number of decimal places to keep.

    Returns:
        Decimal value or None when invalid or out of bounds.
    """
    if value is None or value == "":
        return None
    try:
        dec_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    quantizer = Decimal("1").scaleb(-decimal_places)
    dec_value = dec_value.quantize(quantizer, rounding=ROUND_HALF_UP)
    if max_digits is not None:
        digits_only = format(dec_value.copy_abs(), "f").replace(".", "").lstrip("0") or "0"
        if len(digits_only) > max_digits:
            return None
    return dec_value


_TRANSFORMS = {
    "to_string": _to_string,
    "to_decimal": _to_decimal,
    "to_latitude": lambda value: _format_decimal(value, max_digits=8, decimal_places=6),
    "to_longitude": lambda value: _format_decimal(value, max_digits=9, decimal_places=6),
    "strip": _strip,
    "lower": _lower,
    "upper": _upper,
}
