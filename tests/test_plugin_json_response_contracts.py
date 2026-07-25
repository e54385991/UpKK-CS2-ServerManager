from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from api.routes import github_plugins, plugin_market
from modules import (
    InstalledPluginAnalysisResponse,
    PluginCategory,
    PluginUninstallRequest,
    PluginUninstallResponse,
)


def test_selected_plugin_routes_publish_exact_success_models() -> None:
    app = FastAPI()
    app.include_router(github_plugins.router)
    app.include_router(plugin_market.router)
    paths = app.openapi()["paths"]

    contracts = {
        (
            "/api/github-plugins/servers/{server_id}/analyze-installed-plugins",
            "get",
        ): "InstalledPluginAnalysisResponse",
        ("/api/plugin-market/categories", "get"): "PluginCategoriesResponse",
        (
            "/api/plugin-market/plugins-for-dependencies",
            "get",
        ): "PluginDependencyOptionsResponse",
        (
            "/api/plugin-market/plugins/{plugin_id}/uninstall",
            "post",
        ): "PluginUninstallResponse",
    }

    for (path, method), model_name in contracts.items():
        response = paths[path][method]["responses"]["200"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model_name}"
        }


@pytest.mark.asyncio
async def test_installed_plugin_analysis_body_is_unchanged(monkeypatch) -> None:
    server = SimpleNamespace(game_directory="/srv/game")

    class Manager:
        disconnected = False

        async def connect(self, _server):
            return True, "connected"

        async def execute_command(self, command: str, **_kwargs: Any):
            if command.startswith("test -d "):
                return True, "exists", ""
            if "-type f -exec" in command:
                return True, "10 ./file.txt", ""
            if "-type d " in command:
                return True, "./subdir", ""
            raise AssertionError(f"unexpected command: {command}")

        async def disconnect(self):
            self.disconnected = True

    manager = Manager()
    monkeypatch.setattr(
        github_plugins,
        "get_server_and_verify_ownership",
        AsyncMock(return_value=server),
    )

    result = await github_plugins.analyze_installed_plugins(
        server_id=17,
        directory="addons",
        db=object(),  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=3, is_admin=False),  # type: ignore[arg-type]
        ssh_manager=manager,  # type: ignore[arg-type]
    )

    assert isinstance(result, InstalledPluginAnalysisResponse)
    assert result.model_dump() == {
        "success": True,
        "files": [
            {"path": "addons/file.txt", "size": 10, "is_dir": False},
            {"path": "addons/subdir", "size": 0, "is_dir": True},
        ],
        "total_size": 10,
        "error": None,
    }
    assert manager.disconnected is True


@pytest.mark.asyncio
async def test_category_and_dependency_option_bodies_are_unchanged(monkeypatch) -> None:
    categories = await plugin_market.list_categories(
        current_user=SimpleNamespace(id=3),  # type: ignore[arg-type]
    )
    assert categories.model_dump() == {
        "success": True,
        "categories": [
            {"value": category.value, "name": category.value.replace("_", " ").title()}
            for category in PluginCategory
        ],
    }

    async def search_plugins(_cls, _db, **_kwargs):
        return (
            [
                SimpleNamespace(id=1, title="First"),
                SimpleNamespace(id=2, title="Excluded"),
                SimpleNamespace(id=3, title="Third"),
            ],
            3,
        )

    monkeypatch.setattr(
        plugin_market.MarketPlugin,
        "search_plugins",
        classmethod(search_plugins),
    )
    dependencies = await plugin_market.list_plugins_for_dependencies(
        exclude_id=2,
        search="plugin",
        db=object(),  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=1, is_admin=True),  # type: ignore[arg-type]
    )

    assert dependencies.model_dump() == {
        "success": True,
        "plugins": [
            {"id": 1, "title": "First"},
            {"id": 3, "title": "Third"},
        ],
    }


@pytest.mark.asyncio
async def test_market_uninstall_body_is_unchanged(monkeypatch) -> None:
    async def get_plugin(_cls, _db, plugin_id):
        assert plugin_id == 9
        return SimpleNamespace(id=plugin_id)

    expected = PluginUninstallResponse(
        success=False,
        message="one file could not be removed",
        deleted_files=1,
        failed_files=["addons/plugin.dll"],
    )
    uninstall = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        plugin_market.MarketPlugin,
        "get_by_id",
        classmethod(get_plugin),
    )
    monkeypatch.setattr(plugin_market, "get_server_for_user", AsyncMock())
    monkeypatch.setattr(github_plugins, "uninstall_plugin", uninstall)
    manager = SimpleNamespace(disconnect=AsyncMock())

    result = await plugin_market.uninstall_market_plugin(
        plugin_id=9,
        server_id=17,
        request=PluginUninstallRequest(files_to_delete=["addons/plugin.dll"]),
        db=object(),  # type: ignore[arg-type]
        current_user=SimpleNamespace(id=3, is_admin=False),  # type: ignore[arg-type]
        ssh_manager=manager,  # type: ignore[arg-type]
    )

    assert result is expected
    assert result.model_dump() == {
        "success": False,
        "message": "one file could not be removed",
        "deleted_files": 1,
        "failed_files": ["addons/plugin.dll"],
    }
    uninstall.assert_awaited_once()
