"""Marketplace release selection, verified sync and fleet submission regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.contracts.v1.plugin_release_sync import MarketReleaseDeployRequest
from api.routes.v1 import plugin_release_sync as routes
from modules.models import ManagedPlugin, MarketPlugin
from services.plugins import market_release_sync as sync_service
from services.plugins import market_release_update as update
from services.plugins.github_assets import GitHubPlanError
from services.plugins.release_selection import MarketReleaseSync, SyncedAsset, release_assets

REPO = "https://github.com/komashchenko/ClientCvarValue"
PACKAGE = "ClientCvarValue-v1.1.0-linux.tar.gz"
DEBUG = "ClientCvarValue-v1.1.0-linux-relwithdebinfo.tar.gz"
ENTRY = "addons/clientcvarvalue/bin/linuxsteamrt64/clientcvarvalue.so"


def _asset(name, index=11, tag="v1.1.0"):
    return {
        "name": name,
        "id": index,
        "browser_download_url": f"{REPO}/releases/download/{tag}/{name}",
    }


def _release(*names):
    return {
        "id": 23,
        "tag_name": "v1.1.0",
        "assets": [_asset(name, index) for index, name in enumerate(names, 11)],
    }


def _listing(**kwargs):
    return MarketPlugin(id=1, title="ClientCvarValue", github_url=REPO, **kwargs)


def _tracked(**kwargs):
    return ManagedPlugin(
        server_id=1, source_type="market", source_key="1", display_name="ClientCvarValue", **kwargs
    )


def _snapshot(*assets):
    return MarketReleaseSync(
        revision=uuid4().hex,
        repository=REPO.casefold(),
        release_id="23",
        tag="v1.1.0",
        assets=[
            SyncedAsset(
                id=str(i), name=name, url=_asset(name)["browser_download_url"], sha256="a" * 64
            )
            for i, name in enumerate(assets, 11)
        ],
    )


@pytest.mark.asyncio
async def test_client_cvar_value_uses_normal_package_after_archive_rename(monkeypatch):
    inspected = AsyncMock(return_value={"archive_sha256": "a" * 64, "entrypoints": [ENTRY]})
    monkeypatch.setattr(update, "inspect_market_package", inspected)
    release = _release(DEBUG, PACKAGE, "ClientCvarValue-v1.1.0-windows.zip")
    assert [asset["name"] for asset in release_assets(release)] == [PACKAGE]
    result = await update.resolve_market_update(
        _listing(),
        _tracked(
            installed_version="v1.0.10", installed_asset_name="linux.zip", asset_glob="linux.zip"
        ),
        release,
        None,
        None,
    )
    assert result["asset"]["name"] == PACKAGE
    inspected.assert_awaited_once()


@pytest.mark.asyncio
async def test_renamed_package_requires_unique_matching_installed_entrypoints(monkeypatch):
    item = _tracked(
        installed_version="v1.0.10", installed_asset_name="old-name.zip", asset_glob="old-name.zip"
    )
    renamed = _release("totally-renamed.tar.gz")
    inspect = AsyncMock(return_value={"entrypoints": [ENTRY]})
    monkeypatch.setattr(update, "inspect_market_package", inspect)
    old = _release("old-name.zip")
    monkeypatch.setattr(update, "fetch_release", AsyncMock(return_value=old))
    assert (await update.resolve_market_update(_listing(), item, renamed, None, None))["asset"][
        "name"
    ] == "totally-renamed.tar.gz"
    inspect.side_effect = [{"entrypoints": ["addons/unrelated.so"]}, {"entrypoints": [ENTRY]}]
    with pytest.raises(GitHubPlanError, match="Unable to verify renamed"):
        await update.resolve_market_update(_listing(), item, renamed, None, None)
    with pytest.raises(GitHubPlanError, match="found 2"):
        await update.resolve_market_update(
            _listing(), item, _release("new-one.zip", "new-two.zip"), None, None
        )


@pytest.mark.asyncio
async def test_pinned_runtime_family_digest_and_no_downgrade(monkeypatch):
    names = ("plugin-steamrt3.zip", "plugin-steamrt4.zip")
    release = _release(*names)
    pinned = _snapshot(*names)
    inspect = AsyncMock(return_value={"archive_sha256": "a" * 64, "entrypoints": [ENTRY]})
    monkeypatch.setattr(update, "inspect_market_package", inspect)
    listing = _listing()
    item = _tracked(installed_version="v1.0.10")
    for runtime, name in zip(("steamrt3", "steamrt4"), names, strict=True):
        result = await update.resolve_market_update(
            listing, item, release, None, {"recommended_steam_runtime": runtime}, pinned=pinned
        )
        assert result["asset"]["name"] == name
    release["assets"][0]["id"] = 999
    with pytest.raises(GitHubPlanError, match="asset changed"):
        await update.resolve_market_update(
            listing, item, release, None, {"recommended_steam_runtime": "steamrt3"}, pinned=pinned
        )
    newer = _tracked(installed_version="v1.2.0", installed_asset_name=PACKAGE)
    assert (await update.resolve_market_update(listing, newer, release, None, None))["no_op"]
    debug = _tracked(installed_version="v1.1.0", installed_asset_name=DEBUG)
    assert (await update.resolve_market_update(listing, debug, release, None, None))["no_op"]


@pytest.mark.asyncio
async def test_preview_requires_explicit_selection_and_save_detects_changed_assets(monkeypatch):
    user = SimpleNamespace(id=9)
    plugin = _listing(version="v1.0.10", release_asset_glob="linux.zip")
    release = _release(PACKAGE, DEBUG, "second-linux.zip")
    monkeypatch.setattr(
        sync_service, "_load_listing", AsyncMock(return_value=(plugin, "test-token"))
    )
    fetch = AsyncMock(return_value=release)
    monkeypatch.setattr(sync_service, "fetch_release", fetch)
    monkeypatch.setattr(
        sync_service,
        "inspect_market_package",
        AsyncMock(
            return_value={
                "archive_sha256": "a" * 64,
                "entrypoints": [ENTRY],
            }
        ),
    )
    cache_set = AsyncMock(return_value=True)
    monkeypatch.setattr(sync_service.redis_manager, "set", cache_set)
    ambiguous = await sync_service.preview_release_sync(1, user, rule="*", asset_name=None)
    assert not ambiguous["preview_id"] and ambiguous["reason"]
    preview = await sync_service.preview_release_sync(1, user, rule=None, asset_name=PACKAGE)
    assert preview["selected_assets"] == [PACKAGE]
    assert preview["rule"] == "ClientCvarValue-*-linux.tar.gz"
    stored = cache_set.await_args.args[1]
    monkeypatch.setattr(sync_service.redis_manager, "get", AsyncMock(return_value=stored))
    fetch.return_value = _release("replaced-linux.zip")
    with pytest.raises(GitHubPlanError, match="asset changed"):
        await sync_service.save_release_sync(1, user, preview["preview_id"])


@pytest.mark.asyncio
async def test_batch_reports_partial_rejection_and_reuses_accepted_request(monkeypatch):
    user = SimpleNamespace(id=1)
    pinned = _snapshot(PACKAGE)
    monkeypatch.setattr(
        routes,
        "release_targets",
        AsyncMock(
            return_value=(
                pinned,
                [
                    {"server_id": 1, "server_name": "one", "managed_plugin_id": 101},
                    {"server_id": 2, "server_name": "two", "managed_plugin_id": 102},
                ],
            )
        ),
    )
    cache = {}

    async def get(key):
        return cache.get(key)

    async def set_value(key, value, **_):
        cache[key] = value
        return True

    monkeypatch.setattr(routes.redis_manager, "get", get)
    monkeypatch.setattr(routes.redis_manager, "set", set_value)
    monkeypatch.setattr(routes.redis_manager, "acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(routes.redis_manager, "release_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "record_audit_event", AsyncMock())
    monkeypatch.setattr(
        routes,
        "reject_stuck_lock_unless_active",
        AsyncMock(
            side_effect=[
                None,
                HTTPException(409, "locked"),
            ]
        ),
    )
    enqueue = AsyncMock(
        return_value={
            "operation_id": "operation-1",
            "server_id": 1,
            "actor_user_id": 1,
            "action": "plugin_auto_update",
            "status": "queued",
            "started_at": "2026-11-26T00:00:00",
        }
    )
    monkeypatch.setattr(routes, "enqueue_plugin_auto_update", enqueue)
    body = MarketReleaseDeployRequest(
        revision=pinned.revision, request_id=uuid4(), server_ids=[1, 2, 3, 1]
    )
    first = await routes.deploy(1, body, user, None)
    second = await routes.deploy(1, body, user, None)
    assert first == second
    assert first.items[0].operation.operation_id == "operation-1"
    assert first.items[1].error == "locked"
    assert first.items[2].error == "No eligible tracked installation"
    enqueue.assert_awaited_once_with(server_id=1, actor_user_id=1, plugin_id=101, pinned=pinned)


@pytest.mark.asyncio
async def test_manual_history_keeps_debug_asset_selectable_but_recommends_normal_package(
    monkeypatch,
):
    from api.contracts.v1.plugin_github import GitHubReleaseView
    from services.plugins import market_release_choices as choices

    old = {"id": 22, "tag_name": "v1.0.10", "assets": [_asset("linux.zip", tag="v1.0.10")]}
    stable = _release(DEBUG, PACKAGE, "windows.zip")
    monkeypatch.setattr(choices, "_release_history", AsyncMock(return_value=[old, stable]))
    monkeypatch.setattr(choices, "fetch_release", AsyncMock(return_value=stable))
    releases = [
        GitHubReleaseView.model_validate(item)
        for item in await choices.market_release_choices(_listing(), None, None, count=10)
    ]
    assert [release.tag_name for release in releases] == ["v1.1.0", "v1.0.10"]
    assert releases[0].default_asset_name == PACKAGE
    assert {asset.name for asset in releases[0].assets} == {PACKAGE, DEBUG}
    assert releases[1].default_asset_name == "linux.zip"
