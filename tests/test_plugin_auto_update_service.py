"""Focused tests for managed plugin release selection and safe defaults."""

from unittest.mock import AsyncMock

import pytest

from modules.models import AuthType, CustomCommand, ManagedPlugin, Server, User
from services.maintenance_lock import maintenance_lock_service
from services.plugin_auto_update_service import (
    PluginAutoUpdateService,
    canonical_repo_url,
    derive_asset_glob,
)


def test_repo_and_asset_metadata_are_normalized():
    assert canonical_repo_url("https://github.com/Owner/Repo/") == "https://github.com/Owner/Repo"
    assert derive_asset_glob("plugin-v1.2.3-linux.zip", "v1.2.3") == "plugin-*-linux.zip"
    with pytest.raises(ValueError):
        canonical_repo_url("https://example.com/Owner/Repo")


def test_managed_plugin_defaults_to_auto_update_disabled():
    item = ManagedPlugin(
        server_id=1,
        source_type="github",
        source_key="repo",
        display_name="Plugin",
        repo_url="https://github.com/Owner/Repo",
    )
    server = Server(
        id=1,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    assert item.auto_update_enabled is False
    assert item.backup_before_update is False
    assert item.restart_after_update is False
    assert server.enable_plugin_auto_update is False
    assert server.plugin_update_check_interval_hours == 1.0


@pytest.mark.asyncio
async def test_single_plugin_test_update_forces_selected_item(monkeypatch):
    service = PluginAutoUpdateService()
    calls = {}

    async def fake_check(server_id, force=False, plugin_id=None):
        calls.update(server_id=server_id, force=force, plugin_id=plugin_id)
        return {"success": True}

    monkeypatch.setattr(service, "_check_server", fake_check)
    result = await service.check_plugin(7, 42)
    assert result == {"success": True}
    assert calls == {"server_id": 7, "force": True, "plugin_id": 42}


@pytest.mark.asyncio
async def test_latest_release_requires_exactly_one_matching_asset(monkeypatch):
    service = PluginAutoUpdateService()
    item = ManagedPlugin(
        server_id=1,
        source_type="github",
        source_key="repo",
        display_name="Plugin",
        repo_url="https://github.com/Owner/Repo",
        asset_glob="plugin-*-linux.zip",
    )
    user = User(username="u", email="u@example.com", hashed_password="x")

    async def one_asset(*args, **kwargs):
        return (
            True,
            {
                "id": 44,
                "tag_name": "v2.0.0",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "plugin-v2.0.0-linux.zip",
                        "browser_download_url": "https://github.com/Owner/Repo/releases/download/v2.0.0/plugin.zip",
                    }
                ],
            },
            None,
        )

    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", one_asset)
    ok, latest, error = await service._latest_github_release(item, user)
    assert ok is True
    assert error == ""
    assert latest["release_id"] == "44"
    assert latest["version"] == "v2.0.0"

    async def ambiguous(*args, **kwargs):
        data = (await one_asset())[1]
        data["assets"].append(
            {"name": "plugin-debug-linux.zip", "browser_download_url": "https://github.com/x"}
        )
        return True, data, None

    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", ambiguous)
    ok, latest, error = await service._latest_github_release(item, user)
    assert ok is False
    assert latest is None
    assert "exactly one" in error


@pytest.mark.asyncio
async def test_market_release_asset_fallback_handles_changed_version_name(monkeypatch):
    service = PluginAutoUpdateService()
    item = ManagedPlugin(
        server_id=1,
        source_type="market",
        source_key="42",
        display_name="CleanerCS2",
        market_plugin_id=42,
        repo_url="https://github.com/Owner/CleanerCS2",
        asset_glob="MultiAddonManager-*-linux.tar.gz",
    )
    user = User(username="u", email="u@example.com", hashed_password="x")

    async def latest(*args, **kwargs):
        return (
            True,
            {
                "id": 45,
                "tag_name": "v3.0.0",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "MultiAddonManager-3.0.0.tar.gz",
                        "browser_download_url": "https://github.com/Owner/CleanerCS2/releases/download/v3.0.0/MultiAddonManager-3.0.0.tar.gz",
                    }
                ],
            },
            None,
        )

    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", latest)
    ok, release, error = await service._latest_github_release(item, user)
    assert ok is True
    assert error == ""
    assert release["asset"]["name"] == "MultiAddonManager-3.0.0.tar.gz"


@pytest.mark.asyncio
async def test_market_release_asset_fallback_rejects_ambiguous_archives(monkeypatch):
    service = PluginAutoUpdateService()
    item = ManagedPlugin(
        server_id=1,
        source_type="market",
        source_key="42",
        display_name="CleanerCS2",
        market_plugin_id=42,
        repo_url="https://github.com/Owner/CleanerCS2",
        asset_glob="MultiAddonManager-*-linux.tar.gz",
    )
    user = User(username="u", email="u@example.com", hashed_password="x")

    async def latest(*args, **kwargs):
        return (
            True,
            {
                "id": 45,
                "tag_name": "v3.0.0",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "MultiAddonManager-3.0.0.tar.gz",
                        "browser_download_url": "https://github.com/x/a",
                    },
                    {
                        "name": "MultiAddonManager-3.0.0-debug.tar.gz",
                        "browser_download_url": "https://github.com/x/b",
                    },
                ],
            },
            None,
        )

    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", latest)
    ok, release, error = await service._latest_github_release(item, user)
    assert ok is False
    assert release is None
    assert "fallback matched 2" in error


@pytest.mark.asyncio
async def test_same_release_switches_to_newly_required_runtime(monkeypatch):
    service = PluginAutoUpdateService()
    service._redis_status_retry_after = float("inf")
    server = Server(
        id=87,
        user_id=2,
        name="Runtime Switch",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
    )
    user = User(id=2, username="owner", email="runtime@example.com", hashed_password="x")
    item = ManagedPlugin(
        id=4,
        server_id=87,
        source_type="market",
        source_key="44",
        display_name="MultiAddonManager",
        repo_url="https://github.com/Source2ZE/MultiAddonManager",
        market_plugin_id=44,
        installed_release_id="154",
        installed_version="v1.5.4",
        installed_asset_name="MultiAddonManager-v1.5.4-steamrt3.tar.gz",
        asset_glob="MultiAddonManager-*-steamrt3.tar.gz",
        auto_update_enabled=True,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [item]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, object_id):
            if model is Server:
                return server
            if model is User:
                return user
            if model is ManagedPlugin:
                return item
            return None

        async def execute(self, statement):
            return Result()

        async def commit(self):
            return None

        def add(self, value):
            return None

    release = {
        "id": 154,
        "tag_name": "v1.5.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "MultiAddonManager-v1.5.4-steamrt3.tar.gz",
                "browser_download_url": "https://github.com/example/rt3",
            },
            {
                "name": "MultiAddonManager-v1.5.4-steamrt4.tar.gz",
                "browser_download_url": "https://github.com/example/rt4",
            },
        ],
    }
    detection = AsyncMock(
        return_value={
            "recommended_steam_runtime": "steamrt4",
            "detection_source": "glibc",
            "reason": "glibc 2.41 selects SteamRT4",
        }
    )
    install = AsyncMock(return_value=(True, "updated"))
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr(
        "services.plugin_auto_update_service.http_helper.get",
        AsyncMock(return_value=(True, release, None)),
    )
    monkeypatch.setattr("services.linux_runtime_service.detect_linux_runtime_profile", detection)
    monkeypatch.setattr(service, "_install_item", install)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.discord_notification_service.queue_notify",
        lambda *args, **kwargs: True,
    )

    result = await service.check_server(server.id)

    assert result["success"] is True
    install.assert_awaited_once()
    detection.assert_awaited_once_with(server)
    assert item.installed_release_id == "154"
    assert item.installed_asset_name.endswith("steamrt4.tar.gz")
    assert item.asset_glob == "MultiAddonManager-*-steamrt4.tar.gz"


@pytest.mark.asyncio
async def test_unknown_runtime_only_blocks_paired_release(monkeypatch):
    service = PluginAutoUpdateService()
    user = User(username="u", email="unknown-runtime@example.com", hashed_password="x")
    paired = ManagedPlugin(
        server_id=1,
        source_type="market",
        source_key="1",
        display_name="Paired",
        repo_url="https://github.com/Owner/Paired",
        market_plugin_id=1,
        asset_glob="Paired-*.tar.gz",
    )
    ordinary = ManagedPlugin(
        server_id=1,
        source_type="github",
        source_key="2",
        display_name="Ordinary",
        repo_url="https://github.com/Owner/Ordinary",
        asset_glob="Ordinary-*.tar.gz",
    )

    async def release(url, **kwargs):
        if url.endswith("/Paired/releases/latest"):
            assets = [
                {"name": "Paired-steamrt3.tar.gz", "browser_download_url": "rt3"},
                {"name": "Paired-steamrt4.tar.gz", "browser_download_url": "rt4"},
            ]
        else:
            assets = [{"name": "Ordinary-v2.tar.gz", "browser_download_url": "ordinary"}]
        return True, {"id": 2, "tag_name": "v2", "assets": assets}, None

    monkeypatch.setattr("services.plugin_auto_update_service.http_helper.get", release)
    profile = {"recommended_steam_runtime": None}

    paired_ok, _, paired_error = await service._latest_github_release(paired, user, profile)
    ordinary_ok, ordinary_release, ordinary_error = await service._latest_github_release(
        ordinary, user, profile
    )

    assert paired_ok is False
    assert "could not be detected" in paired_error
    assert ordinary_ok is True
    assert ordinary_error == ""
    assert ordinary_release["asset"]["name"] == "Ordinary-v2.tar.gz"


@pytest.mark.asyncio
async def test_backup_failure_skips_only_plugins_that_requested_backup(monkeypatch):
    service = PluginAutoUpdateService()
    service._redis_status_retry_after = float("inf")
    server = Server(
        id=88,
        user_id=2,
        name="Backup Guard",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
    )
    user = User(id=2, username="owner", email="owner@example.com", hashed_password="x")
    item = ManagedPlugin(
        id=5,
        server_id=88,
        source_type="github",
        source_key="repo",
        display_name="Plugin",
        repo_url="https://github.com/Owner/Repo",
        asset_glob="plugin-*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
        backup_before_update=True,
    )
    unprotected_item = ManagedPlugin(
        id=7,
        server_id=88,
        source_type="github",
        source_key="repo2",
        display_name="Unprotected",
        repo_url="https://github.com/Owner/Repo2",
        asset_glob="plugin2-*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
        backup_before_update=False,
    )
    items = {item.id: item, unprotected_item.id: unprotected_item}

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [item, unprotected_item]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, object_id):
            if model is Server:
                return server
            if model is User:
                return user
            if model is ManagedPlugin:
                return items[object_id]

        async def execute(self, statement):
            return Result()

        async def commit(self):
            return None

        def add(self, value):
            return None

    class BackupFails:
        async def backup_plugins(self, server):
            return False, "disk full"

    async def latest(*args):
        return (
            True,
            {
                "release_id": "2",
                "version": "v2",
                "asset": {"name": "plugin-v2.zip", "browser_download_url": "https://github.com/x"},
            },
            "",
        )

    installed_names = []

    async def install(server, user, managed, latest):
        installed_names.append(managed.display_name)
        return True, "installed"

    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr("services.plugin_auto_update_service.SSHManager", BackupFails)
    monkeypatch.setattr(service, "_latest_github_release", latest)
    monkeypatch.setattr(service, "_install_item", install)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.discord_notification_service.queue_notify",
        lambda *a, **k: True,
    )

    result = await service.check_server(88)
    assert result["success"] is False
    assert "failures" in result["message"].lower()
    assert installed_names == ["Unprotected"]
    assert item.last_status == "failed"
    assert unprotected_item.last_status == "success"
    protected_result = next(entry for entry in result["results"] if entry["name"] == "Plugin")
    assert "backup failed" in protected_result["message"].lower()


@pytest.mark.asyncio
async def test_shared_maintenance_lock_prevents_overlapping_update():
    service = PluginAutoUpdateService()
    lock = maintenance_lock_service.get(999)
    await lock.acquire()
    try:
        result = await service.check_server(999, force=True)
    finally:
        lock.release()
    assert result["success"] is False
    assert "already running" in result["message"]


@pytest.mark.asyncio
async def test_plugin_restart_policy_restarts_running_server_once_for_multiple_items(monkeypatch):
    service = PluginAutoUpdateService()
    service._redis_status_retry_after = float("inf")
    server = Server(
        id=89,
        user_id=2,
        name="Framework Restart",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
    )
    user = User(id=2, username="owner2", email="owner2@example.com", hashed_password="x")
    item = ManagedPlugin(
        id=6,
        server_id=89,
        source_type="framework",
        source_key="counterstrikesharp",
        display_name="CounterStrikeSharp",
        repo_url="https://github.com/roflmuffin/CounterStrikeSharp",
        framework_key="counterstrikesharp",
        asset_glob="counterstrikesharp-with-runtime-linux*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
        restart_after_update=True,
    )
    ordinary_item = ManagedPlugin(
        id=8,
        server_id=89,
        source_type="github",
        source_key="ordinary",
        display_name="Ordinary Plugin",
        repo_url="https://github.com/owner/plugin",
        asset_glob="plugin-*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
        restart_after_update=True,
    )
    items = {item.id: item, ordinary_item.id: ordinary_item}

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [item, ordinary_item]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, object_id):
            if model is Server:
                return server
            if model is User:
                return user
            if model is ManagedPlugin:
                return items[object_id]

        async def execute(self, statement):
            return Result()

        async def commit(self):
            return None

        def add(self, value):
            return None

    calls = {"status": 0, "backup": 0, "preflight": 0, "stop": 0, "start": 0}

    class Manager:
        async def get_server_status(self, server):
            calls["status"] += 1
            return True, "running"

        async def backup_plugins(self, server):
            calls["backup"] += 1
            return True, "/backup/plugins.tar.gz"

        async def check_session_manager_available(self, server):
            calls["preflight"] += 1
            return True, "screen is available"

        async def stop_server(self, server):
            calls["stop"] += 1
            return True, "stopped"

        async def start_server(self, server):
            calls["start"] += 1
            return True, "started"

    async def latest(*args):
        return (
            True,
            {
                "release_id": "2",
                "version": "v2",
                "asset": {"name": "css.zip", "browser_download_url": "https://github.com/x"},
            },
            "",
        )

    async def install(*args):
        return True, "updated"

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr("services.plugin_auto_update_service.SSHManager", Manager)
    monkeypatch.setattr("services.plugin_auto_update_service.asyncio.sleep", no_sleep)
    monkeypatch.setattr(service, "_latest_github_release", latest)
    monkeypatch.setattr(service, "_install_item", install)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.discord_notification_service.queue_notify",
        lambda *a, **k: True,
    )

    result = await service.check_server(89)
    assert result["success"] is True
    assert calls == {
        "status": 1,
        "backup": 0,
        "preflight": 1,
        "stop": 1,
        "start": 1,
    }
    assert result["restart"]["message"] == "started"
    assert (await service.get_status(89))["state"] == "completed"


@pytest.mark.asyncio
async def test_post_update_commands_run_once_after_all_plugins_finish(monkeypatch):
    service = PluginAutoUpdateService()
    service._redis_status_retry_after = float("inf")
    server = Server(
        id=90,
        user_id=3,
        name="Post Commands",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
        enable_plugin_post_update_commands=True,
        plugin_post_update_command_ids=[11, 12],
    )
    user = User(id=3, username="owner3", email="owner3@example.com", hashed_password="x")
    item = ManagedPlugin(
        id=9,
        server_id=90,
        source_type="github",
        source_key="repo",
        display_name="Plugin A",
        repo_url="https://github.com/Owner/RepoA",
        asset_glob="plugin-*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
    )
    second = ManagedPlugin(
        id=10,
        server_id=90,
        source_type="github",
        source_key="repo2",
        display_name="Plugin B",
        repo_url="https://github.com/Owner/RepoB",
        asset_glob="plugin2-*.zip",
        installed_release_id="1",
        installed_version="v1",
        auto_update_enabled=True,
    )
    commands = {
        11: CustomCommand(
            id=11,
            user_id=3,
            server_id=90,
            name="Reload maps",
            target="game_process",
            commands="css_plugins reload",
        ),
        12: CustomCommand(
            id=12,
            user_id=3,
            server_id=90,
            name="Host check",
            target="host",
            commands="echo ready",
        ),
    }
    items = {item.id: item, second.id: second}
    install_order = []
    command_order = []

    class Result:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self

        def all(self):
            return self._values

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, object_id):
            if model is Server:
                return server
            if model is User:
                return user
            if model is ManagedPlugin:
                return items[object_id]

        async def execute(self, statement):
            entities = []
            try:
                entities = [
                    description.get("entity")
                    for description in getattr(statement, "column_descriptions", [])
                ]
            except Exception:
                entities = []
            if CustomCommand in entities:
                return Result([commands[11], commands[12]])
            return Result([item, second])

        async def commit(self):
            return None

        def add(self, value):
            return None

    class Manager:
        async def get_server_status(self, server):
            return True, "stopped"

        async def backup_plugins(self, server):
            return True, "ok"

    async def latest(*args):
        return (
            True,
            {
                "release_id": "2",
                "version": "v2",
                "asset": {"name": "plugin-v2.zip", "browser_download_url": "https://github.com/x"},
            },
            "",
        )

    async def install(server, user, managed, latest):
        install_order.append(managed.display_name)
        return True, "updated"

    async def execute_commands(server, target, command_text):
        command_order.append((target, command_text))
        assert len(install_order) == 2
        return {
            "success": True,
            "message": "Executed 1 command(s) successfully",
            "target": target,
            "results": [
                {"index": 1, "command": command_text, "success": True, "stdout": "", "stderr": ""}
            ],
        }

    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr("services.plugin_auto_update_service.SSHManager", Manager)
    monkeypatch.setattr(service, "_latest_github_release", latest)
    monkeypatch.setattr(service, "_install_item", install)
    monkeypatch.setattr(
        "services.plugin_auto_update_service.execute_custom_commands", execute_commands
    )
    monkeypatch.setattr(
        "services.plugin_auto_update_service.discord_notification_service.queue_notify",
        lambda *a, **k: True,
    )

    result = await service.check_server(90)
    assert result["success"] is True
    assert install_order == ["Plugin A", "Plugin B"]
    assert command_order == [
        ("game_process", "css_plugins reload"),
        ("host", "echo ready"),
    ]
    assert result["post_update_commands"]["success"] is True
    assert len(result["post_update_commands"]["results"]) == 2


@pytest.mark.asyncio
async def test_check_all_servers_enqueues_due_update(monkeypatch):
    service = PluginAutoUpdateService()
    server = Server(
        id=12,
        user_id=3,
        name="Due",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
        plugin_update_check_interval_hours=1.0,
        last_plugin_update_check=None,
        is_ssh_down=False,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [server]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return Result()

    enqueue = AsyncMock(return_value={"operation_id": "op-1"})
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr(service, "_plugin_update_already_queued", AsyncMock(return_value=False))
    monkeypatch.setattr("services.operation_enqueue.enqueue_plugin_auto_update", enqueue)

    await service.check_all_servers()

    enqueue.assert_awaited_once_with(server_id=12, actor_user_id=3, force=False)


@pytest.mark.asyncio
async def test_check_all_servers_skips_when_already_queued(monkeypatch):
    service = PluginAutoUpdateService()
    server = Server(
        id=12,
        user_id=3,
        name="Due",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        enable_plugin_auto_update=True,
        plugin_update_check_interval_hours=1.0,
        last_plugin_update_check=None,
        is_ssh_down=False,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [server]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return Result()

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "services.plugin_auto_update_service.async_session_maker", lambda: Session()
    )
    monkeypatch.setattr(service, "_plugin_update_already_queued", AsyncMock(return_value=True))
    monkeypatch.setattr("services.operation_enqueue.enqueue_plugin_auto_update", enqueue)

    await service.check_all_servers()

    enqueue.assert_not_awaited()
