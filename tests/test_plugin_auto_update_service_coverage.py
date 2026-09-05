"""覆盖插件自动更新服务的状态、版本解析和批处理策略。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import plugin_auto_update_service as module


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, *, server=None, item=None, user=None, rows=()):
        self.server = server
        self.item = item
        self.user = user
        self.rows = list(rows)
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, model, _key):
        name = getattr(model, "__name__", "")
        return {"Server": self.server, "ManagedPlugin": self.item, "User": self.user}.get(name)

    async def execute(self, _query):
        return _Result(self.rows)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _server(**overrides):
    values = dict(
        id=7,
        user_id=3,
        enable_plugin_auto_update=True,
        last_plugin_update_check=None,
        plugin_update_check_interval_hours=1.0,
        enable_plugin_post_update_commands=False,
        plugin_post_update_command_ids=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _item(**overrides):
    values = dict(
        id=11,
        server_id=7,
        source_type="github",
        display_name="Demo",
        repo_url="https://github.com/acme/demo",
        asset_glob="demo-*-linux.zip",
        framework_key=None,
        installed_release_id="old",
        installed_version="v1",
        installed_asset_name="demo-v1-linux.zip",
        exclude_dirs=[],
        exclude_files=[],
        custom_install_path=None,
        backup_before_update=False,
        restart_after_update=False,
        auto_update_enabled=True,
        config_policy="preserve",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_status_cache_redis_sink_and_lifecycle(monkeypatch):
    service = module.PluginAutoUpdateService()
    service._redis_status_retry_after = float("inf")
    sink = AsyncMock()
    service.set_progress_sink(7, sink)
    status = await service._publish_status(7, state="running", message="working", log="line")
    assert status["state"] == "running" and sink.await_count == 1
    service.clear_progress_sink(7)
    assert 7 not in service._progress_sinks
    assert (await service.get_status(7))["message"] == "working"

    redis = module.redis_manager
    service._redis_status_retry_after = 0
    monkeypatch.setattr(redis, "get", AsyncMock(return_value={"state": "completed"}))
    assert (await service.get_status(7))["state"] == "completed"
    monkeypatch.setattr(redis, "get", AsyncMock(side_effect=RuntimeError("offline")))
    assert (await service.get_status(8))["state"] == "idle"
    monkeypatch.setattr(redis, "set", AsyncMock(side_effect=RuntimeError("offline")))
    service._redis_status_retry_after = 0
    await service._publish_status(8, state="failed", log="error")

    service._loop = AsyncMock()
    await service.start()
    task = service.task
    await service.start()
    await service.stop()
    assert task is not None and service.task is None
    await service.stop()


@pytest.mark.asyncio
async def test_due_queue_checks_and_asset_helpers(monkeypatch):
    service = module.PluginAutoUpdateService()
    assert service._due(None, 1)
    now = module.get_current_time()
    assert service._due(now, 1) is False
    assert service._is_windows_asset("plugin-windows.zip")
    assert not service._is_windows_asset("plugin-linux.zip")
    assert service._archive_extension("plugin.tar.gz") == ".tar.gz"
    assert service._archive_extension("plugin.bin") is None
    item = _item(source_type="github", market_plugin_id=None)
    assert service._fallback_release_assets(item, [{"name": "other.tar.gz"}], "demo-*.zip") == []
    market = _item(source_type="market", market_plugin_id=1)
    assets = [
        {"name": "demo-linux.zip"},
        {"name": "demo-windows.zip"},
        {"name": "demo-alt.tar.gz"},
    ]
    assert service._fallback_release_assets(market, assets, "demo-*.zip")

    server = _server()
    server.should_skip_background_checks = lambda: False
    server.user_id = 3
    db = _Db(rows=[server])
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(service, "_due", staticmethod(lambda *_args: True))
    monkeypatch.setattr(service, "_plugin_update_already_queued", AsyncMock(return_value=False))
    enqueue_module = importlib.import_module("services.operation_enqueue")
    enqueue = AsyncMock()
    monkeypatch.setattr(enqueue_module, "enqueue_plugin_auto_update", enqueue)
    await service.check_all_servers()
    enqueue.assert_awaited_once()
    monkeypatch.setattr(service, "_plugin_update_already_queued", AsyncMock(return_value=True))
    await service.check_all_servers()


@pytest.mark.asyncio
async def test_latest_release_metamod_and_install_item_paths(monkeypatch):
    service = module.PluginAutoUpdateService()
    user = SimpleNamespace(has_github_token=False, github_token=None)
    item = _item()
    release = {
        "id": 4,
        "tag_name": "v2",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "demo-v2-linux.zip"}],
    }
    monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(True, release, None)))
    ok, latest, error = await service._latest_github_release(item, user)
    assert ok and latest["version"] == "v2" and not error
    monkeypatch.setattr(module.http_helper, "get", AsyncMock(return_value=(False, None, "bad")))
    assert (await service._latest_github_release(item, user))[0] is False
    assert (await service._latest_github_release(_item(repo_url=None), user))[2]

    class _SSH:
        async def connect(self, _server):
            return True, "ok"

        async def disconnect(self):
            return None

        async def _fetch_latest_metamod_url(self, _value):
            return True, "https://github.com/acme/metamod/releases/download/v1/metamod.tar.gz"

        async def update_metamod(self, _server):
            return True, "updated"

        async def update_counterstrikesharp(self, _server):
            return True, "updated"

    monkeypatch.setattr(module, "SSHManager", lambda: _SSH())
    ok, latest, _ = await service._latest_metamod(_server())
    assert ok and latest["asset"]["name"] == "metamod.tar.gz"
    framework = _item(framework_key="metamod")
    assert await service._install_item(_server(), user, framework, latest) == (True, "updated")
    framework.framework_key = "counterstrikesharp"
    assert await service._install_item(_server(), user, framework, latest) == (True, "updated")

    db = _Db()
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(
        module,
        "install_github_plugin",
        AsyncMock(return_value=SimpleNamespace(success=True, message="ok")),
    )
    result = await service._install_item(_server(), user, _item(), latest)
    assert result == (True, "ok")


@pytest.mark.asyncio
async def test_upgrade_plan_post_commands_and_framework_records(monkeypatch):
    service = module.PluginAutoUpdateService()
    server = _server()
    user = SimpleNamespace(id=3, is_active=True)
    item = _item()
    db = _Db(server=server, item=item, user=user)
    monkeypatch.setattr(module, "async_session_maker", lambda: db)
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value={"reason": "x"}),
    )
    monkeypatch.setattr(
        service,
        "_latest_github_release",
        AsyncMock(
            return_value=(
                True,
                {"release_id": "new", "version": "v2", "asset": {"name": "demo-v2-linux.zip"}},
                "",
            )
        ),
    )
    plan = await service.build_plugin_upgrade_plan(7, 11)
    assert plan["no_op"] is False and plan["config_exclusions"]
    db.item = None
    with pytest.raises(ValueError, match="not found"):
        await service.build_plugin_upgrade_plan(7, 11)

    command = SimpleNamespace(id=1, name="Restart", target="server", commands="restart")
    command_db = _Db(rows=[command])
    monkeypatch.setattr(module, "async_session_maker", lambda: command_db)
    service._redis_status_retry_after = float("inf")
    monkeypatch.setattr(
        module,
        "execute_custom_commands",
        AsyncMock(return_value={"success": True, "message": "ok", "results": ["ok"]}),
    )
    monkeypatch.setattr(module, "format_custom_command_log", lambda *_args: "log")
    result = await service._execute_post_update_commands(server, [1, 2, "bad"])
    assert not result["success"] and len(result["results"]) == 2
    command_db.rows = [command]
    monkeypatch.setattr(
        module, "execute_custom_commands", AsyncMock(side_effect=RuntimeError("boom"))
    )
    result = await service._execute_post_update_commands(server, [1])
    assert not result["success"] and "boom" in result["results"][0]["message"]
    assert service._normalize_command_ids([1, "1", 0, "x", None, 2]) == [1, 2]

    upsert = AsyncMock()
    monkeypatch.setattr(module, "upsert_managed_plugin", upsert)
    monkeypatch.setattr(
        module.plugin_auto_update_service,
        "_latest_metamod",
        AsyncMock(return_value=(False, None, "offline")),
    )
    await module.record_framework_installation(server, user, "metamod")
    assert upsert.await_args.kwargs["installed_version"] == "unknown"
    monkeypatch.setattr(
        module.plugin_auto_update_service,
        "_latest_github_release",
        AsyncMock(return_value=(False, None, "offline")),
    )
    await module.record_known_github_installation(
        server, user, "https://github.com/acme/demo/", "Demo", "demo-*.zip"
    )
    assert upsert.await_count == 2
