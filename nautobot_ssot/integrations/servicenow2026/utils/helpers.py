"""General helpers for ServiceNow 2026 integration."""

from typing import List, Optional


def parse_csv(value: Optional[str]) -> List[str]:
    """Return a list of values from a comma-delimited string."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_truthy(value: Optional[str]) -> bool:
    """Return True if a string value represents a truthy flag."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
