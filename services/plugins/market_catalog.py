"""Catalog helpers for marketplace listings and declared dependencies."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules import DependencyInfo, MarketPlugin, MarketPluginResponse
from services.plugins.common import parse_dependency_ids


class MissingDependencyError(LookupError):
    """Raised when a declared marketplace dependency ID is not in the catalog."""

    def __init__(self, plugin_id: int) -> None:
        super().__init__(f"Dependency plugin with ID {plugin_id} not found")
        self.plugin_id = plugin_id


async def validate_dependencies(db: AsyncSession, dependency_ids: list[int]) -> None:
    """Confirm every declared dependency ID exists in the marketplace catalog."""
    dependencies = await MarketPlugin.get_by_ids(db, dependency_ids)
    existing_ids = {plugin.id for plugin in dependencies}
    missing_id = next((dep_id for dep_id in dependency_ids if dep_id not in existing_ids), None)
    if missing_id is not None:
        raise MissingDependencyError(missing_id)


async def populate_dependency_details(
    db: AsyncSession, plugins: List[MarketPlugin]
) -> List[MarketPluginResponse]:
    """Attach ``dependency_details`` while preserving the stored ID order."""
    responses = []
    parsed_dependencies: list[Optional[list[int]]] = []
    all_dependency_ids: list[int] = []

    for plugin in plugins:
        if plugin.dependencies:
            try:
                dep_ids = parse_dependency_ids(plugin.dependencies, unique=False)
            except ValueError:
                dep_ids = None
        else:
            dep_ids = []
        parsed_dependencies.append(dep_ids)
        if dep_ids:
            all_dependency_ids.extend(dep_ids)

    dependencies = await MarketPlugin.get_by_ids(db, all_dependency_ids)
    dependencies_by_id = {plugin.id: plugin for plugin in dependencies}

    for plugin, dep_ids in zip(plugins, parsed_dependencies, strict=True):
        response = MarketPluginResponse.model_validate(plugin)
        if dep_ids:
            dependency_details = [
                DependencyInfo(id=dep_plugin.id, title=dep_plugin.title)
                for dep_id in dep_ids
                if (dep_plugin := dependencies_by_id.get(dep_id)) is not None
            ]
            response.dependency_details = dependency_details or None

        responses.append(response)

    return responses
