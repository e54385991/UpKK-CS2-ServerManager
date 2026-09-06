"""Unit coverage for portable plugin-catalog URL mapping and import strategies."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models.plugins import (
    MarketPlugin,
    PluginCategory,
    PluginConflictRule,
    PluginFramework,
)
from modules.schemas.plugins import (
    PluginCatalogConflict,
    PluginCatalogEntry,
    PluginCatalogImportRequest,
)
from services.plugin_catalog import (
    catalog_github_url,
    catalog_lookup_key,
    collect_export_bundle,
    conflict_to_catalog_item,
    delete_market_plugin,
    ensure_default_plugin_catalog,
    import_plugin_catalog,
    load_default_plugin_catalog,
    plugin_to_catalog_entry,
)


def test_default_plugin_catalog_is_portable_and_self_contained():
    request = load_default_plugin_catalog()
    assert request.format == "upkk-cs2-plugin-catalog"
    assert request.version == 1
    assert request.plugins
    urls = {catalog_lookup_key(plugin.github_url) for plugin in request.plugins}
    assert "https://github.com/kzglobalteam/cs2kz-metamod" in urls
    assert "https://github.com/roflmuffin/counterstrikesharp" in urls
    for plugin in request.plugins:
        for dependency in plugin.dependencies:
            assert catalog_lookup_key(dependency) in urls, dependency


def test_catalog_github_url_strips_releases_and_git_suffix():
    assert (
        catalog_github_url("https://github.com/Owner/Repo/releases/")
        == "https://github.com/Owner/Repo"
    )
    assert (
        catalog_github_url("https://github.com/Owner/Repo.git") == "https://github.com/Owner/Repo"
    )
    assert catalog_lookup_key("https://GitHub.com/Owner/Repo/") == "https://github.com/owner/repo"


def test_catalog_github_url_rejects_non_github():
    with pytest.raises(ValueError, match="GitHub repository URL"):
        catalog_github_url("https://gitlab.com/owner/repo")


def test_plugin_to_catalog_entry_uses_github_urls_not_ids():
    metamod = MarketPlugin(
        id=1,
        github_url="https://github.com/alliedmodders/metamod-source/",
        title="Metamod",
        category=PluginCategory.LIBRARY,
        dependencies=None,
    )
    matchzy = MarketPlugin(
        id=11,
        github_url="https://github.com/shobhit-pathak/MatchZy",
        title="MatchZy",
        category=PluginCategory.GAME_MODE,
        dependencies="1,99",
        is_recommended=True,
    )
    entry = plugin_to_catalog_entry(matchzy, {1: metamod, 11: matchzy})
    assert entry is not None
    assert entry.github_url == "https://github.com/shobhit-pathak/MatchZy"
    assert entry.dependencies == ["https://github.com/alliedmodders/metamod-source"]
    assert entry.category == "game_mode"
    dumped = entry.model_dump()
    assert "id" not in dumped
    assert "11" not in dumped["dependencies"]


def test_plugin_to_catalog_entry_carries_the_marketplace_section():
    swiftly = MarketPlugin(
        id=12,
        github_url="https://github.com/example/swiftly-plugin",
        title="Swiftly Plugin",
        category=PluginCategory.UTILITY,
        framework=PluginFramework.SWIFTLY,
    )
    entry = plugin_to_catalog_entry(swiftly, {12: swiftly})
    assert entry is not None
    assert entry.framework == "swiftly"


def test_catalog_entry_defaults_to_counterstrikesharp():
    assert _entry().framework == "counterstrikesharp"


@pytest.mark.asyncio
async def test_import_creates_and_updates_the_marketplace_section(monkeypatch):
    existing = MarketPlugin(
        id=9,
        github_url="https://github.com/example/kept",
        title="Kept",
        category=PluginCategory.OTHER,
    )
    session = _FakeSession(plugins=[existing])
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(side_effect=lambda _db: list(session.rules)),
    )

    summary = await import_plugin_catalog(
        session,
        PluginCatalogImportRequest(
            plugins=[
                _entry(
                    github_url="https://github.com/example/kept",
                    title="Kept",
                    framework="swiftly",
                ),
                _entry(
                    github_url="https://github.com/example/fresh",
                    title="Fresh",
                    framework="swiftly",
                ),
                _entry(
                    github_url="https://github.com/example/broken",
                    title="Broken",
                    framework="sourcemod",
                ),
            ],
            conflict_strategy="update",
        ),
    )

    assert existing.framework is PluginFramework.SWIFTLY
    created = next(item for item in session.plugins if item.title == "Fresh")
    assert created.framework is PluginFramework.SWIFTLY
    failure = next(item for item in summary.results if item.name == "Broken")
    assert failure.action == "failed"
    assert failure.message == "Invalid framework: sourcemod"


def test_conflict_to_catalog_item_uses_github_urls():
    left = MarketPlugin(
        id=1,
        github_url="https://github.com/example/alpha",
        title="Alpha",
        category=PluginCategory.UTILITY,
    )
    right = MarketPlugin(
        id=2,
        github_url="https://github.com/example/beta",
        title="Beta",
        category=PluginCategory.UTILITY,
    )
    item = conflict_to_catalog_item(
        PluginConflictRule(
            plugin_a_id=1,
            plugin_b_id=2,
            severity="hard",
            reason="incompatible",
            is_enabled=True,
        ),
        {1: left, 2: right},
    )
    assert item is not None
    assert {item.plugin_a_url, item.plugin_b_url} == {
        "https://github.com/example/alpha",
        "https://github.com/example/beta",
    }
    assert item.severity == "hard"


class _FakeSession:
    def __init__(self, plugins=None, rules=None):
        self.plugins = list(plugins or [])
        self.rules = list(rules or [])
        self._next_plugin_id = max((item.id or 0 for item in self.plugins), default=0) + 1
        self._next_rule_id = max((item.id or 0 for item in self.rules), default=0) + 1
        self.committed = False

    def add(self, obj):
        if isinstance(obj, MarketPlugin) and obj not in self.plugins:
            self.plugins.append(obj)
        if isinstance(obj, PluginConflictRule) and obj not in self.rules:
            self.rules.append(obj)

    async def flush(self):
        for plugin in self.plugins:
            if plugin.id is None:
                plugin.id = self._next_plugin_id
                self._next_plugin_id += 1
        for rule in self.rules:
            if rule.id is None:
                rule.id = self._next_rule_id
                self._next_rule_id += 1

    async def commit(self):
        await self.flush()
        self.committed = True

    async def delete(self, obj):
        if obj in self.plugins:
            self.plugins.remove(obj)
        if obj in self.rules:
            self.rules.remove(obj)


def _entry(**overrides) -> PluginCatalogEntry:
    values = {
        "github_url": "https://github.com/example/new-plugin",
        "title": "New Plugin",
        "category": "utility",
        "dependencies": [],
    }
    values.update(overrides)
    return PluginCatalogEntry(**values)


@pytest.mark.asyncio
async def test_export_bundle_contains_no_local_ids(monkeypatch):
    metamod = MarketPlugin(
        id=4,
        github_url="https://github.com/alliedmodders/metamod-source",
        title="Metamod",
        category=PluginCategory.LIBRARY,
    )
    css = MarketPlugin(
        id=5,
        github_url="https://github.com/roflmuffin/CounterStrikeSharp",
        title="CSS",
        category=PluginCategory.LIBRARY,
        dependencies="4",
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(return_value=[metamod, css]),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(
            return_value=[
                PluginConflictRule(
                    id=9,
                    plugin_a_id=4,
                    plugin_b_id=5,
                    severity="warning",
                    reason="order",
                    is_enabled=True,
                )
            ]
        ),
    )
    bundle = await collect_export_bundle(SimpleNamespace())
    assert bundle.format == "upkk-cs2-plugin-catalog"
    assert bundle.version == 1
    assert [item.github_url for item in bundle.plugins] == [
        "https://github.com/alliedmodders/metamod-source",
        "https://github.com/roflmuffin/CounterStrikeSharp",
    ]
    assert bundle.plugins[1].dependencies == ["https://github.com/alliedmodders/metamod-source"]
    payload = bundle.model_dump(mode="json")
    assert "plugin_a_id" not in str(payload)
    assert payload["conflicts"][0]["plugin_a_url"].startswith("https://github.com/")


@pytest.mark.asyncio
async def test_import_skip_keeps_existing_and_creates_missing(monkeypatch):
    existing = MarketPlugin(
        id=8,
        github_url="https://github.com/example/kept",
        title="Kept",
        category=PluginCategory.OTHER,
        description="original",
    )
    session = _FakeSession(plugins=[existing])
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(side_effect=lambda _db: list(session.rules)),
    )
    summary = await import_plugin_catalog(
        session,
        PluginCatalogImportRequest(
            plugins=[
                _entry(
                    github_url="https://github.com/example/kept",
                    title="Changed",
                    description="new",
                    category="admin",
                ),
                _entry(github_url="https://github.com/example/fresh", title="Fresh"),
            ],
            conflict_strategy="skip",
        ),
    )
    assert session.committed is True
    assert summary.skipped == 1
    assert summary.imported == 1
    assert existing.title == "Kept"
    assert existing.description == "original"
    created = next(item for item in session.plugins if item.title == "Fresh")
    assert created.github_url == "https://github.com/example/fresh"
    assert created.id is not None


@pytest.mark.asyncio
async def test_import_update_rewrites_metadata_and_url_dependencies(monkeypatch):
    metamod = MarketPlugin(
        id=1,
        github_url="https://github.com/alliedmodders/metamod-source",
        title="Metamod",
        category=PluginCategory.LIBRARY,
    )
    css = MarketPlugin(
        id=2,
        github_url="https://github.com/roflmuffin/CounterStrikeSharp",
        title="Old CSS",
        category=PluginCategory.OTHER,
        dependencies=None,
    )
    session = _FakeSession(plugins=[metamod, css])
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(side_effect=lambda _db: list(session.rules)),
    )
    summary = await import_plugin_catalog(
        session,
        PluginCatalogImportRequest(
            plugins=[
                _entry(
                    github_url="https://github.com/roflmuffin/CounterStrikeSharp",
                    title="CounterStrikeSharp",
                    category="library",
                    is_recommended=True,
                    dependencies=["https://github.com/alliedmodders/metamod-source"],
                )
            ],
            conflict_strategy="update",
        ),
    )
    assert summary.updated == 1
    assert css.title == "CounterStrikeSharp"
    assert css.category == PluginCategory.LIBRARY
    assert css.is_recommended is True
    assert css.dependencies == "1"


@pytest.mark.asyncio
async def test_import_update_applies_conflict_rules_by_url(monkeypatch):
    left = MarketPlugin(
        id=3,
        github_url="https://github.com/example/alpha",
        title="Alpha",
        category=PluginCategory.UTILITY,
    )
    right = MarketPlugin(
        id=7,
        github_url="https://github.com/example/beta",
        title="Beta",
        category=PluginCategory.UTILITY,
    )
    existing_rule = PluginConflictRule(
        id=1,
        plugin_a_id=3,
        plugin_b_id=7,
        severity="warning",
        reason="old",
        is_enabled=True,
    )
    session = _FakeSession(plugins=[left, right], rules=[existing_rule])
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(side_effect=lambda _db: list(session.rules)),
    )
    summary = await import_plugin_catalog(
        session,
        PluginCatalogImportRequest(
            plugins=[],
            conflicts=[
                PluginCatalogConflict(
                    plugin_a_url="https://github.com/example/beta",
                    plugin_b_url="https://github.com/example/alpha",
                    severity="hard",
                    reason="cannot coexist",
                    is_enabled=False,
                )
            ],
            conflict_strategy="update",
        ),
    )
    assert summary.updated == 1
    assert existing_rule.severity == "hard"
    assert existing_rule.reason == "cannot coexist"
    assert existing_rule.is_enabled is False


@pytest.mark.asyncio
async def test_ensure_default_plugin_catalog_imports_when_empty(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )
    monkeypatch.setattr(
        "services.plugin_catalog._load_conflict_rules",
        AsyncMock(side_effect=lambda _db: list(session.rules)),
    )
    summary = await ensure_default_plugin_catalog(session)
    assert summary is not None
    assert summary.imported == len(load_default_plugin_catalog().plugins)
    assert summary.failed == 0
    assert session.committed is True
    titles = {plugin.title for plugin in session.plugins}
    assert "cs2kz-metamod" in titles
    assert "CounterStrikeSharp" in titles


@pytest.mark.asyncio
async def test_ensure_default_plugin_catalog_skips_when_populated(monkeypatch):
    existing = MarketPlugin(
        id=3,
        github_url="https://github.com/example/already-there",
        title="Already",
        category=PluginCategory.OTHER,
    )
    session = _FakeSession(plugins=[existing])
    load = AsyncMock(return_value=[existing])
    monkeypatch.setattr("services.plugin_catalog._load_market_plugins", load)
    summary = await ensure_default_plugin_catalog(session)
    assert summary is None
    assert session.committed is False
    assert session.plugins == [existing]
    load.assert_awaited_once()


def _catalog_session(session: _FakeSession, monkeypatch):
    monkeypatch.setattr(
        "services.plugin_catalog._load_market_plugins",
        AsyncMock(side_effect=lambda _db: list(session.plugins)),
    )


@pytest.mark.asyncio
async def test_delete_market_plugin_returns_none_when_missing(monkeypatch):
    session = _FakeSession()
    _catalog_session(session, monkeypatch)
    assert await delete_market_plugin(session, 99) is None
    assert session.committed is False


@pytest.mark.asyncio
async def test_delete_market_plugin_strips_dependency_ids(monkeypatch):
    lib = MarketPlugin(
        id=1,
        github_url="https://github.com/example/lib",
        title="Lib",
        category=PluginCategory.LIBRARY,
    )
    target = MarketPlugin(
        id=2,
        github_url="https://github.com/example/gone",
        title="Gone",
        category=PluginCategory.UTILITY,
    )
    dependent = MarketPlugin(
        id=3,
        github_url="https://github.com/example/app",
        title="App",
        category=PluginCategory.GAME_MODE,
        dependencies="1,2",
    )
    last_dep = MarketPlugin(
        id=4,
        github_url="https://github.com/example/solo",
        title="Solo",
        category=PluginCategory.OTHER,
        dependencies="2",
    )
    session = _FakeSession(plugins=[lib, target, dependent, last_dep])
    _catalog_session(session, monkeypatch)

    deleted = await delete_market_plugin(session, 2)

    assert deleted is target
    assert target not in session.plugins
    assert dependent.dependencies == "1"
    assert last_dep.dependencies is None
    assert session.committed is True
