"""Coverage for queued operation adapters using isolated in-memory doubles."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.v1.operation_runner import (
    cleanup,
    diagnostics,
    game_mode,
    host,
    maintenance,
    market,
    server,
)
from services.game_mode_install_service import GameModePlanError
from services.maintenance_lock import OperationBusyError
from services.plugin_conflict_service import PluginPlanError
from services.server_operation_hub import ServerOperationConflict


class _Hub:
    def __init__(self, record=None):
        self.record = record or {"operation_id": "op-1", "server_id": 7, "actor_user_id": 9}
        self.finished: list[tuple] = []
        self.emitted: list[tuple] = []
        self.running: list[str] = []

    async def create(self, **kwargs):
        return {**self.record, **kwargs}

    async def get(self, _operation_id):
        return self.record

    async def mark_running(self, operation_id):
        self.running.append(operation_id)

    async def finish(self, operation_id, **kwargs):
        self.finished.append((operation_id, kwargs))

    async def emit(self, operation_id, *args, **kwargs):
        self.emitted.append((operation_id, args, kwargs))


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, user=None, *, execute=None):
        self.user = user or SimpleNamespace(id=9, is_active=True)
        self.execute_result = execute
        self.deleted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _item_id):
        return self.user

    async def execute(self, _query):
        return self.execute_result or SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None

    async def delete(self, value):
        self.deleted.append(value)


def _record(**overrides):
    value = {"operation_id": "op-1", "server_id": 7, "actor_user_id": 9, "action": "start"}
    value.update(overrides)
    return value


def _install(monkeypatch, module, *, record=None, user=None):
    hub = _Hub(record or _record())
    session = _Session(user)
    monkeypatch.setattr(module, "server_operation_hub", hub)
    monkeypatch.setattr(module, "async_session_maker", lambda: session)
    monkeypatch.setattr(
        module,
        "maintenance_lock_service",
        SimpleNamespace(get=lambda *_a, **_k: _Lock()),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "require_server_access",
        AsyncMock(return_value=SimpleNamespace(id=7, user_id=9)),
        raising=False,
    )
    monkeypatch.setattr(module, "_dispatch", AsyncMock(side_effect=lambda record, _factory: record))
    if hasattr(module, "_audit_terminal"):
        monkeypatch.setattr(module, "_audit_terminal", AsyncMock())
    return hub, session


@pytest.mark.asyncio
async def test_server_runner_enqueue_and_success_paths(monkeypatch):
    hub, _session = _install(monkeypatch, server)
    queued = await server.enqueue_game_console_command(
        server_id=7, command="  say secret-token  ", actor_user_id=9
    )
    assert queued["action"] == "send_game_command"
    assert queued["command"] == "game-console say secret-token"
    monkeypatch.setattr(
        server,
        "execute_custom_commands",
        AsyncMock(return_value={"success": True, "message": "ok"}),
    )
    await server.run_game_console_command(operation_id="op-1", command="status")
    assert hub.finished[-1][1]["success"] is True

    monkeypatch.setattr(
        server,
        "execute_server_action",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True, message="started", data={"status": "running"}
            )
        ),
    )
    await server.enqueue_server_operation(server_id=7, action="start", actor_user_id=9)
    await server.run_server_operation(operation_id="op-1")
    assert hub.finished[-1][1]["server_status"] == "running"


@pytest.mark.asyncio
async def test_server_runner_covers_validation_auth_and_failures(monkeypatch):
    with pytest.raises(HTTPException, match="Invalid action"):
        await server.enqueue_server_operation(server_id=7, action="invalid", actor_user_id=9)

    hub, _session = _install(monkeypatch, server, user=SimpleNamespace(id=9, is_active=False))
    await server.run_game_console_command(operation_id="op-1", command="status")
    assert hub.finished[-1][1]["success"] is False

    for error in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        HTTPException(400, "bad"),
    ):
        hub, _session = _install(monkeypatch, server)
        monkeypatch.setattr(server, "require_server_access", AsyncMock(side_effect=error))
        await server.run_game_console_command(operation_id="op-1", command="status")
        assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_maintenance_runner_covers_enqueue_variants_and_disabled_result(monkeypatch):
    hub, _session = _install(monkeypatch, maintenance)
    await maintenance.enqueue_plugin_auto_update(server_id=7, actor_user_id=9, plugin_id=4)
    await maintenance.enqueue_plugin_auto_update(server_id=7, actor_user_id=9, force=True)
    await maintenance.enqueue_plugin_auto_update(server_id=7, actor_user_id=9, force=False)
    service = SimpleNamespace(
        set_progress_sink=lambda *_a: None,
        clear_progress_sink=lambda *_a: None,
        check_server=AsyncMock(
            return_value={"success": False, "message": "Plugin auto-update is disabled"}
        ),
    )
    monkeypatch.setattr("services.plugin_auto_update_service.plugin_auto_update_service", service)
    await maintenance.run_plugin_auto_update(operation_id="op-1", plugin_id=None, force=True)
    assert hub.finished[-1][1]["success"] is True
    assert service.check_server.await_count == 1


@pytest.mark.asyncio
async def test_game_mode_runner_covers_success_and_planner_errors(monkeypatch):
    hub, _session = _install(monkeypatch, game_mode)
    execute = AsyncMock(return_value={"success": True, "message": "installed"})
    monkeypatch.setattr(game_mode, "execute_game_mode_plan", execute)
    queued = await game_mode.enqueue_game_mode_install(
        server_id=7,
        mode_id="retakes",
        actor_user_id=9,
        wipe_addons=True,
        wipe_addons_acknowledged=True,
        plan_hash="hash",
        acknowledge_warning_rule_ids=[1],
    )
    assert "--wipe-addons" in queued["command"]
    await game_mode.run_game_mode_install(
        operation_id="op-1",
        mode_id="retakes",
        wipe_addons=True,
        plan_hash="hash",
        acknowledge_warning_rule_ids=[1],
    )
    assert hub.finished[-1][1]["success"] is True

    for error in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        GameModePlanError("stale"),
        PluginPlanError("conflict"),
        HTTPException(409, "no"),
    ):
        hub, _session = _install(monkeypatch, game_mode)
        monkeypatch.setattr(game_mode, "execute_game_mode_plan", AsyncMock(side_effect=error))
        await game_mode.run_game_mode_install(
            operation_id="op-1",
            mode_id="retakes",
            wipe_addons=False,
            plan_hash="hash",
            acknowledge_warning_rule_ids=[],
        )
        assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_cleanup_runners_cover_success_error_and_exception_paths(monkeypatch):
    hub, _session = _install(monkeypatch, cleanup)
    monkeypatch.setattr(cleanup, "_progress_emitter", lambda _operation_id: AsyncMock())
    monkeypatch.setattr(cleanup, "SSHManager", lambda: SimpleNamespace(disconnect=AsyncMock()))
    monkeypatch.setattr(
        cleanup.game_cleanup_service,
        "delete",
        AsyncMock(return_value=(True, {"message": "deleted", "deleted_count": 2}, "")),
    )
    await cleanup.run_cleanup_delete(
        operation_id="op-1", mode="safe", paths=["a"], confirmation_text="ok"
    )
    assert hub.finished[-1][1]["success"] is True
    monkeypatch.setattr(
        cleanup.game_cleanup_service, "delete", AsyncMock(return_value=(False, {}, "bad path"))
    )
    await cleanup.run_cleanup_delete(
        operation_id="op-1", mode="safe", paths=["a"], confirmation_text=None
    )
    assert hub.finished[-1][1]["message"] == "bad path"

    monkeypatch.setattr(cleanup, "normalize_targets", lambda targets: list(targets))
    monkeypatch.setattr(
        cleanup.system_cleanup_service,
        "apply",
        AsyncMock(return_value={"success": True, "applied": ["logs"], "deleted_count": 1}),
    )
    await cleanup.run_cleanup_system(operation_id="op-1", targets=["logs"], retain_days=2)
    assert hub.finished[-1][1]["success"] is True
    monkeypatch.setattr(
        cleanup, "normalize_targets", lambda _targets: (_ for _ in ()).throw(ValueError("target"))
    )
    await cleanup.run_cleanup_system(operation_id="op-1", targets=["bad"], retain_days=None)
    assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_host_runners_cover_apt_mirror_and_s3_restore_failure(monkeypatch, tmp_path):
    hub, _session = _install(monkeypatch, host)
    server_obj = SimpleNamespace(id=7, apt_mirror=None, status=None, game_directory="/srv/cs2")
    monkeypatch.setattr(host, "require_server_access", AsyncMock(return_value=server_obj))
    monkeypatch.setattr(host, "get_server_owner_user", AsyncMock(return_value=server_obj))
    manager = SimpleNamespace(connect=AsyncMock(return_value=(True, "ok")), disconnect=AsyncMock())
    monkeypatch.setattr(host, "SSHManager", lambda: manager)
    monkeypatch.setattr(
        host,
        "ensure_steamcmd_packages",
        AsyncMock(return_value=SimpleNamespace(success=True, message="ready", apt_mirror="mirror")),
    )
    await host.run_apply_apt_mirror(operation_id="op-1", mirror="mirror")
    assert hub.finished[-1][1]["success"] is True
    assert server_obj.apt_mirror == "mirror"

    backup = SimpleNamespace(
        safe_object_filename=lambda _key: "archive.tar.gz",
        validate_object_key=lambda *_a: False,
        download_backup=AsyncMock(),
    )
    monkeypatch.setattr(host, "s3_backup_service", backup)
    monkeypatch.setattr(host, "SSHManager", lambda: SimpleNamespace(disconnect=AsyncMock()))
    await host.run_s3_restore(operation_id="op-1", object_key="bad")
    assert hub.finished[-1][1]["message"] == "Selected S3 backup does not belong to this server"


@pytest.mark.asyncio
async def test_host_apt_runner_covers_queue_connection_and_terminal_failures(monkeypatch):
    hub, _session = _install(monkeypatch, host)
    monkeypatch.setattr(host, "_dispatch", AsyncMock(side_effect=lambda record, _factory: record))
    queued = await host.enqueue_apply_apt_mirror(
        server_id=7, mirror="https://mirror.example", actor_user_id=9
    )
    assert queued["action"] == "apply_apt_mirror"

    server_obj = SimpleNamespace(id=7, apt_mirror=None, status=None)
    monkeypatch.setattr(host, "require_server_access", AsyncMock(return_value=server_obj))
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(False, "connection refused")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(host, "SSHManager", lambda: manager)
    await host.run_apply_apt_mirror(operation_id="op-1", mirror="mirror")
    assert hub.finished[-1][1]["message"] == "connection refused"

    manager.connect.return_value = (True, "connected")
    monkeypatch.setattr(
        host,
        "ensure_steamcmd_packages",
        AsyncMock(
            return_value=SimpleNamespace(success=False, message="apt failed", apt_mirror=None)
        ),
    )
    await host.run_apply_apt_mirror(operation_id="op-1", mirror="mirror")
    assert hub.finished[-1][1]["success"] is False
    assert manager.disconnect.await_count == 1

    for error in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        HTTPException(status_code=409, detail={"reason": "bad"}),
        RuntimeError("unexpected"),
    ):
        hub, _session = _install(monkeypatch, host)
        monkeypatch.setattr(host, "require_server_access", AsyncMock(side_effect=error))
        await host.run_apply_apt_mirror(operation_id="op-1", mirror="mirror")
        assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_s3_restore_worker_covers_success_and_each_remote_stage(monkeypatch):
    server_obj = SimpleNamespace(id=7, game_directory="/srv/cs2", status=None)
    owner = SimpleNamespace(id=9)
    backup = SimpleNamespace(
        safe_object_filename=lambda _key: "plugins.tar.gz",
        validate_object_key=lambda *_args: True,
        download_backup=AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr(host, "s3_backup_service", backup)
    monkeypatch.setattr(host.uuid, "uuid4", lambda: SimpleNamespace(hex="abcdef0123456789"))

    class _Manager:
        last_plugin_backup = "/srv/backup.tar.gz"

        def __init__(self, result=(True, "")):
            self.result = result
            self.disconnect = AsyncMock()
            self.backup_plugins = AsyncMock(return_value=(True, "backup"))
            self.upload_file = AsyncMock(return_value=(True, "uploaded"))
            self.extract_archive = AsyncMock(return_value=(True, "extracted"))

    manager = _Manager()
    monkeypatch.setattr(host, "SSHManager", lambda: manager)
    hub, _session = _install(monkeypatch, host)
    monkeypatch.setattr(host, "require_server_access", AsyncMock(return_value=server_obj))
    monkeypatch.setattr(host, "get_server_owner_user", AsyncMock(return_value=owner))
    await host.run_s3_restore(operation_id="op-1", object_key="prefix/archive.tar.gz")
    assert hub.finished[-1][1]["success"] is True
    assert "Safety backup" in hub.finished[-1][1]["message"]
    assert manager.upload_file.await_count == 1 and manager.extract_archive.await_count == 1

    for stage, result in (
        ("download", (False, "download bad")),
        ("backup", (False, "backup bad")),
        ("upload", (False, "upload bad")),
        ("extract", (False, "extract bad")),
    ):
        hub, _session = _install(monkeypatch, host)
        manager = _Manager()
        monkeypatch.setattr(host, "SSHManager", lambda _manager=manager: _manager)
        if stage == "download":
            backup.download_backup = AsyncMock(return_value=result)
        elif stage == "backup":
            manager.backup_plugins = AsyncMock(return_value=result)
        elif stage == "upload":
            manager.upload_file = AsyncMock(return_value=result)
        else:
            manager.extract_archive = AsyncMock(return_value=result)
        await host.run_s3_restore(operation_id="op-1", object_key="archive.tar.gz")
        assert hub.finished[-1][1]["success"] is False
        await manager.disconnect()
        backup.download_backup = AsyncMock(return_value=(True, ""))


@pytest.mark.asyncio
async def test_s3_restore_worker_handles_inactive_and_exception_cleanup(monkeypatch):
    hub, _session = _install(monkeypatch, host, user=SimpleNamespace(is_active=False))
    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(host, "SSHManager", lambda: manager)
    await host.run_s3_restore(operation_id="op-1", object_key="archive.tar.gz")
    assert hub.finished[-1][1]["success"] is False and manager.disconnect.await_count == 1

    for error in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        HTTPException(status_code=400, detail="bad"),
        RuntimeError("unexpected"),
    ):
        hub, _session = _install(monkeypatch, host)
        manager = SimpleNamespace(disconnect=AsyncMock())
        monkeypatch.setattr(host, "SSHManager", lambda _manager=manager: _manager)
        monkeypatch.setattr(host, "require_server_access", AsyncMock(side_effect=error))
        await host.run_s3_restore(operation_id="op-1", object_key="archive.tar.gz")
        assert hub.finished[-1][1]["success"] is False
        assert manager.disconnect.await_count == 1


@pytest.mark.asyncio
async def test_diagnostics_helpers_and_workers_cover_payload_states(monkeypatch):
    assert diagnostics._diagnostic_success({"status": "completed"}) == (True, "completed")
    assert diagnostics._diagnostic_success({"status": "failed", "error": "broken"}) == (
        False,
        "broken",
    )
    assert diagnostics._diagnostic_success({}) == (True, "Plugin diagnostic finished")
    hub, _session = _install(monkeypatch, diagnostics)
    service = importlib.import_module("services.plugin_diagnostic_service")
    monkeypatch.setattr(
        service,
        "execute_diagnostic_plan",
        AsyncMock(return_value={"id": "d1", "status": "completed"}),
    )
    await diagnostics.run_plugin_diagnostic_execute(
        operation_id="op-1", scope="both", expected_plan_hash="h"
    )
    assert hub.finished[-1][1]["success"] is True
    monkeypatch.setattr(
        service, "restore_diagnostic_run", AsyncMock(return_value={"status": "interrupted"})
    )
    await diagnostics.run_plugin_diagnostic_restore(operation_id="op-1", diagnostic_id="d1")
    assert hub.finished[-1][1]["success"] is False
    monkeypatch.setattr(service, "restore_diagnostic_run", AsyncMock())
    await diagnostics.run_plugin_diagnostic_resume(
        operation_id="op-1", diagnostic_id="d1", scope="both", expected_plan_hash="h"
    )
    assert hub.finished[-1][1]["message"] == "completed"


@pytest.mark.asyncio
async def test_market_runners_cover_plugin_install_github_install_and_uninstall(monkeypatch):
    hub, session = _install(monkeypatch, market)
    monkeypatch.setattr(
        market,
        "execute_plugin_install_plan",
        AsyncMock(return_value={"success": True, "message": "installed"}),
    )
    queued = await market.enqueue_plugin_install(
        server_id=7, plugin_id=4, actor_user_id=9, acknowledge_warning_rule_ids=[], plan_hash="h"
    )
    assert queued["action"] == "install_plugin"
    await market.run_plugin_install(
        operation_id="op-1", plugin_id=4, acknowledge_warning_rule_ids=[], plan_hash="h"
    )
    assert hub.finished[-1][1]["success"] is True

    monkeypatch.setattr(
        market,
        "execute_github_install_plan",
        AsyncMock(return_value={"success": True, "message": "github"}),
    )
    await market.enqueue_github_plugin_install(
        server_id=7,
        actor_user_id=9,
        repo_url="https://github.com/a/b",
        mode="install",
        asset_name=None,
        config_policy="preserve",
        recipe_id=None,
        source_prefix=None,
        target_prefix=None,
        exclude_dirs=[],
        exclude_files=[],
        expected_plan_hash="h",
        acknowledge_warning_rule_ids=[],
        acknowledge_unknown_compatibility=False,
    )
    await market.run_github_plugin_install(
        operation_id="op-1",
        repo_url="https://github.com/a/b",
        mode="install",
        asset_name=None,
        config_policy="preserve",
        recipe_id=None,
        source_prefix=None,
        target_prefix=None,
        exclude_dirs=[],
        exclude_files=[],
        expected_plan_hash="h",
        acknowledge_warning_rule_ids=[],
        acknowledge_unknown_compatibility=False,
    )
    assert hub.finished[-1][1]["success"] is True

    monkeypatch.setattr(
        market,
        "uninstall_plugin_files",
        AsyncMock(return_value={"success": True, "message": "removed"}),
    )
    await market.enqueue_github_plugin_uninstall(
        server_id=7, actor_user_id=9, files_to_delete=["a"], market_plugin_id=None
    )
    await market.run_github_plugin_uninstall(
        operation_id="op-1", files_to_delete=["a"], market_plugin_id=None
    )
    assert hub.finished[-1][1]["message"] == "removed"
    assert session.deleted == []
