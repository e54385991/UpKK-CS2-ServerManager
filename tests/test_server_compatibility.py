"""Compatibility defaults and execstack command safety."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.contracts.v1.server import ServerUpdateRequest
from modules.schemas.server_update import ServerUpdate
from modules.schemas.servers import ServerConfigEntry
from services.game_mode_install_service import _append_kz_dependency_step, _kz_blocking_reasons
from services.server_compatibility import (
    LinuxRelease,
    build_clear_execstack_command,
    effective_clear_execstack,
    execstack_addons_path,
    execstack_cleanup_enabled_for_action,
    execstack_operation_metadata,
    execute_clear_execstack_on_manager,
    maybe_clear_execstack_after_file_action,
    normalize_execstack_targets,
    parse_linux_release,
    run_clear_execstack,
)


def test_release_thresholds_use_real_distribution_id():
    assert parse_linux_release('ID=ubuntu\nVERSION_ID="25.04"').needs_execstack_clear
    assert parse_linux_release("ID=debian\nVERSION_ID=13").needs_execstack_clear
    assert not parse_linux_release("ID=ubuntu\nVERSION_ID=24.04").needs_execstack_clear
    assert parse_linux_release("ID_LIKE=ubuntu\nVERSION_ID=25") is None
    assert not LinuxRelease("ubuntu", "rolling").needs_execstack_clear
    assert parse_linux_release("ID=arch\nVERSION_ID=1") is None
    assert parse_linux_release("ID=ubuntu") is None


def test_effective_setting_prefers_explicit_override_and_unknown_defaults_off():
    assert (
        effective_clear_execstack(
            SimpleNamespace(os_id="ubuntu", os_version="25.04", clear_execstack_override=False)
        )
        is False
    )
    assert (
        effective_clear_execstack(
            SimpleNamespace(os_id="ubuntu", os_version="24.04", clear_execstack_override=True)
        )
        is True
    )
    assert effective_clear_execstack(SimpleNamespace(os_id=None, os_version=None)) is False


def test_trigger_policy_and_custom_targets():
    server = SimpleNamespace(
        os_id="ubuntu",
        os_version="25.04",
        clear_execstack_override=None,
        execstack_fix_on_restart=True,
        execstack_fix_on_framework=False,
        execstack_fix_on_game_update=True,
        game_directory="/srv/cs2",
    )
    assert execstack_cleanup_enabled_for_action(server, "restart") is True
    assert execstack_cleanup_enabled_for_action(server, "install_metamod") is False
    assert execstack_cleanup_enabled_for_action(server, "update") is True
    assert execstack_cleanup_enabled_for_action(server, "other") is False
    assert "foo/bar.so" in build_clear_execstack_command(
        "/srv/my server", ["foo/bar.so", "foo/bar.so"]
    )
    metadata = execstack_operation_metadata(server, "update")
    assert metadata and metadata["clear_execstack"] is True
    assert metadata["clear_execstack_targets"] == [
        "counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so"
    ]
    assert execstack_operation_metadata(server, "restart") is None
    server.clear_execstack_override = False
    assert execstack_operation_metadata(server, "update") == {"clear_execstack": False}


def test_target_validation_rejects_unsafe_values_and_uses_default_for_none():
    assert normalize_execstack_targets(None)
    with pytest.raises(ValueError):
        normalize_execstack_targets("foo.so")
    for value in [[], ["/tmp/plugin.so"], ["../plugin.so"], ["plugin.txt"], [""]]:
        with pytest.raises(ValueError):
            normalize_execstack_targets(value)
    with pytest.raises(ValueError):
        normalize_execstack_targets([f"plugin-{index}.so" for index in range(65)])


def test_http_and_legacy_update_schemas_validate_custom_targets():
    target = "custom/bin/plugin.so"
    assert ServerUpdateRequest(execstack_fix_targets=[target]).execstack_fix_targets == [target]
    assert ServerUpdate(execstack_fix_targets=[target]).execstack_fix_targets == [target]
    assert ServerConfigEntry(
        name="server",
        host="example.test",
        ssh_user="steam",
        ssh_password="secret",
        execstack_fix_targets=[target],
    ).execstack_fix_targets == [target]
    with pytest.raises(ValidationError):
        ServerUpdateRequest(execstack_fix_targets=["/tmp/plugin.so"])


def test_kz_dependency_plan_and_preflight_branches():
    steps: list[dict[str, object]] = []
    mutations: list[dict[str, object]] = []
    _append_kz_dependency_step("kz", {"libssl11": False}, steps, mutations)
    assert steps[0]["status"] == "pending"
    assert mutations[0]["target"] == "libssl.so.1.1 / libcrypto.so.1.1"
    _append_kz_dependency_step("other", {}, steps, mutations)
    assert _kz_blocking_reasons("other", {}, SimpleNamespace()) == []
    reasons = _kz_blocking_reasons(
        "kz",
        {"supported_system": False, "amd64": False, "sudo": False},
        SimpleNamespace(sudo_password=None),
    )
    assert len(reasons) == 3
    assert (
        _kz_blocking_reasons(
            "kz",
            {"supported_system": True, "amd64": True, "sudo": True, "libssl11": False},
            SimpleNamespace(sudo_password=None),
        )
        == []
    )


def test_execstack_command_quotes_paths_and_rejects_escape():
    with pytest.raises(ValueError):
        execstack_addons_path("/srv/../etc")
    command = build_clear_execstack_command("/srv/my server")
    assert (
        "patchelf --clear-execstack '/srv/my server/cs2/game/csgo/addons/counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so'"
        in command
    )
    assert "find " not in command
    assert "[ ! -L" in command


@pytest.mark.asyncio
async def test_run_clear_execstack_closes_connection_on_success_and_failure():
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "connected")),
        execute_sudo_command=AsyncMock(return_value=(True, "fixed", "")),
        disconnect=AsyncMock(),
    )
    server = SimpleNamespace(game_directory="/srv/cs2", sudo_password="pw")
    ok, detail = await run_clear_execstack(manager, server)
    assert ok is True and detail == "fixed"
    manager.disconnect.assert_awaited_once()

    failing = SimpleNamespace(
        connect=AsyncMock(side_effect=RuntimeError("offline")),
        disconnect=AsyncMock(),
    )
    ok, detail = await run_clear_execstack(failing, server)
    assert ok is False and "offline" in detail

    unavailable = SimpleNamespace(connect=AsyncMock(return_value=(False, "denied")))
    ok, detail = await run_clear_execstack(unavailable, server)
    assert (ok, detail) == (False, "denied")

    broken = SimpleNamespace(execute_sudo_command=AsyncMock(side_effect=RuntimeError("broken")))
    ok, detail = await execute_clear_execstack_on_manager(broken, server)
    assert ok is False and "broken" in detail


@pytest.mark.asyncio
async def test_file_action_cleanup_reports_warnings_and_skips_duplicate_update(monkeypatch):
    manager = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())
    report = AsyncMock()
    server = SimpleNamespace(status="running", game_directory="/srv/cs2")
    await maybe_clear_execstack_after_file_action(
        server_id=1,
        action="deploy",
        server=server,
        manager=manager,
        enabled=True,
        report=report,
    )
    report.assert_awaited_once()
    report.reset_mock()
    await maybe_clear_execstack_after_file_action(
        server_id=1,
        action="update",
        server=server,
        manager=manager,
        enabled=True,
        report=report,
    )
    report.assert_not_awaited()
    monkeypatch.setattr(
        "services.server_compatibility.run_clear_execstack",
        AsyncMock(return_value=(False, "permission denied")),
    )
    server.status = "stopped"
    await maybe_clear_execstack_after_file_action(
        server_id=1,
        action="deploy",
        server=server,
        manager=manager,
        enabled=True,
        report=report,
    )
    assert "failed" in report.await_args_list[-1].args[2]
