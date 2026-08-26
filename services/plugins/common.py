"""Pure plugin planning rules shared by installation workflows."""

from __future__ import annotations


class PluginPlanError(ValueError):
    """Raised when a dependency graph or conflict acknowledgement is invalid."""


def parse_dependency_ids(value: str | None) -> list[int]:
    """Parse the legacy comma-separated dependency field without duplicates."""
    if not value:
        return []
    result: list[int] = []
    for item in value.split(","):
        normalized = item.strip()
        if not normalized:
            continue
        if not normalized.isdigit():
            raise PluginPlanError(f"Invalid dependency ID: {normalized}")
        plugin_id = int(normalized)
        if plugin_id not in result:
            result.append(plugin_id)
    return result
