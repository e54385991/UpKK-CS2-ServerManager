"""Pure plugin planning rules shared by installation workflows."""

from __future__ import annotations

from modules.models.plugins import PluginFramework


class PluginPlanError(ValueError):
    """Raised when a dependency graph or conflict acknowledgement is invalid."""


def framework_value(framework: PluginFramework | str | None) -> str:
    """Return the wire value for a marketplace section."""
    if framework is None:
        return PluginFramework.COUNTERSTRIKESHARP.value
    if isinstance(framework, PluginFramework):
        return framework.value
    return str(framework)


def parse_framework(value: str | None) -> PluginFramework:
    """Parse a marketplace section value, accepting the stored enum name too."""
    if value is None or not str(value).strip():
        return PluginFramework.COUNTERSTRIKESHARP
    text = str(value).strip()
    try:
        return PluginFramework(text)
    except ValueError:
        pass
    try:
        return PluginFramework[text.upper()]
    except KeyError:
        raise PluginPlanError(
            "Invalid framework. Valid frameworks: "
            + ", ".join(item.value for item in PluginFramework)
        ) from None


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
