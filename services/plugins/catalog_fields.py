"""Apply administrator edits to a marketplace listing.

Keeping the field mapping here leaves the route with authorization, dependency
validation and response mapping only.
"""

from __future__ import annotations

from modules.models.plugins import MarketPlugin, PluginCategory
from modules.schemas.plugins import MarketPluginUpdate
from services.plugins.common import parse_framework

# Simple pass-through fields: present means "replace", absent means "keep".
_SCALAR_FIELDS = (
    "title",
    "description",
    "author",
    "version",
    "tags",
    "is_recommended",
    "icon_url",
    "custom_install_path",
    "dependencies",
)


def parse_category(value: str) -> PluginCategory:
    """Parse a marketplace category, raising ``ValueError`` when unknown."""
    try:
        return PluginCategory(value)
    except ValueError:
        raise ValueError(
            "Invalid category. Valid categories: "
            + ", ".join(item.value for item in PluginCategory)
        ) from None


def apply_market_plugin_update(plugin: MarketPlugin, request: MarketPluginUpdate) -> None:
    """Copy the submitted fields onto ``plugin``.

    ``None`` means "leave unchanged". Raises ``ValueError`` for an unknown
    category or marketplace section. Dependency IDs are validated against the
    database by the caller before this runs.
    """
    if request.category is not None:
        plugin.category = parse_category(request.category)
    if request.framework is not None:
        plugin.framework = parse_framework(request.framework)
    for name in _SCALAR_FIELDS:
        value = getattr(request, name)
        if value is not None:
            setattr(plugin, name, value)
