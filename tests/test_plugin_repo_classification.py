"""Guessing a listing's runtime and category from its GitHub repository."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from modules.models.plugins import PluginCategory, PluginFramework
from services.plugins import github_repo_info
from services.plugins.repo_classification import (
    detect_plugin_framework,
    suggest_plugin_category,
)


def test_counterstrikesharp_repository_is_detected():
    assert (
        detect_plugin_framework(
            name="CS2-SimpleAdmin",
            description="Admin plugin built on CounterStrikeSharp",
        )
        is PluginFramework.COUNTERSTRIKESHARP
    )


def test_swiftly_repository_is_detected():
    assert (
        detect_plugin_framework(name="swiftly-ranks", description="Ranks for SwiftlyS2")
        is PluginFramework.SWIFTLY
    )


def test_topics_alone_are_enough():
    assert (
        detect_plugin_framework(name="ranks", topics=["cs2", "swiftlys2"])
        is PluginFramework.SWIFTLY
    )


def test_repository_supporting_both_runtimes_is_unrestricted():
    assert (
        detect_plugin_framework(
            name="multi", description="Works with CounterStrikeSharp and SwiftlyS2"
        )
        is PluginFramework.OTHER
    )


def test_metamod_only_repository_is_unrestricted():
    assert (
        detect_plugin_framework(name="StripperCS2", description="A Metamod:Source plugin")
        is PluginFramework.OTHER
    )


def test_unclear_repository_returns_no_guess():
    assert detect_plugin_framework(name="cfg-pack", description="Some configs") is None


def test_only_the_head_of_a_long_readme_is_scanned():
    padding = "unrelated changelog entry. " * 1000
    assert detect_plugin_framework(name="x", readme=padding + "swiftlys2") is None
    assert (
        detect_plugin_framework(name="x", readme="swiftlys2 " + padding) is PluginFramework.SWIFTLY
    )


def test_category_guesses_use_the_first_matching_group():
    assert suggest_plugin_category(description="A shared library for plugins") is (
        PluginCategory.LIBRARY
    )
    assert suggest_plugin_category(description="Retake game mode") is PluginCategory.GAME_MODE
    assert suggest_plugin_category(description="Admin menu and ban system") is (
        PluginCategory.ADMIN
    )
    assert suggest_plugin_category(description="MapChooser with vote system") is (
        PluginCategory.UTILITY
    )
    assert suggest_plugin_category(description="Nothing recognisable here") is None


@pytest.mark.asyncio
async def test_repo_info_returns_the_guessed_classification(monkeypatch):
    readme = base64.b64encode(b"# Ranks\n\nBuilt for SwiftlyS2 servers.").decode()

    async def fake_get(url, **_kwargs):
        if url.endswith("/readme"):
            return True, {"content": readme}, None
        return (
            True,
            {
                "name": "swiftly-ranks",
                "description": "Ranking and statistics",
                "topics": ["cs2", "swiftly"],
            },
            None,
        )

    monkeypatch.setattr(github_repo_info.http_helper, "get", fake_get)

    result = await github_repo_info.fetch_github_repo_info("https://github.com/acme/ranks")

    assert result.success is True
    assert result.framework == "swiftly"
    assert result.category == "utility"
    assert result.topics == ["cs2", "swiftly"]


@pytest.mark.asyncio
async def test_repo_info_leaves_the_classification_empty_when_unclear(monkeypatch):
    async def fake_get(url, **_kwargs):
        if url.endswith("/readme"):
            return False, None, "404"
        return True, {"name": "cfg-pack", "description": "Config files"}, None

    monkeypatch.setattr(github_repo_info.http_helper, "get", fake_get)

    result = await github_repo_info.fetch_github_repo_info("https://github.com/acme/cfg-pack")

    assert result.framework is None
    assert result.category is None
    assert result.topics == []


@pytest.mark.asyncio
async def test_v1_repo_info_exposes_the_classification(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from api.application import create_app
    from modules import get_current_active_user, get_current_user, get_db

    app = create_app(lifespan=None)
    user = SimpleNamespace(id=1, username="root", is_admin=True, is_active=True)
    session = SimpleNamespace(commit=AsyncMock())

    async def override_db():
        yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "api.routes.v1.plugins.get_effective_github_token", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "api.routes.v1.plugins.legacy.fetch_github_repo_info",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                repo_name="swiftly-ranks",
                description="Ranks",
                readme="# Ranks",
                author="acme",
                topics=["swiftly"],
                framework="swiftly",
                category="utility",
                error=None,
            )
        ),
    )

    response = TestClient(app).post(
        "/api/v1/plugins/market/repo-info",
        json={"github_url": "https://github.com/acme/swiftly-ranks"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["framework"] == "swiftly"
    assert body["category"] == "utility"
    assert body["topics"] == ["swiftly"]
