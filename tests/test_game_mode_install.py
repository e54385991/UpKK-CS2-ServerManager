"""Unit coverage for game-mode recipes, launch upsert, and remote helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.execstack import DEFAULT_EXECSTACK_TARGETS
from services.game_mode_execstack import run_planned_execstack_step
from services.game_mode_install_service import (
    _plan_hash,
    build_game_mode_plan,
    catalog_for_server,
)
from services.game_mode_launch import upsert_additional_parameters
from services.game_mode_recipes import GAME_MODE_RECIPES, KZ_RECIPE, get_recipe
from services.game_mode_remote import (
    GameModeRemoteError,
    resolve_addons_directory,
    wait_for_remote_files,
    wipe_addons_directory,
)
from services.server_compatibility import LinuxRelease


def test_kz_recipe_is_the_only_catalog_entry():
    assert list(GAME_MODE_RECIPES) == ["kz"]
    recipe = get_recipe("kz")
    assert recipe.startup_workshop_map == "3082213334"
    assert recipe.maps_append[0].name == "kz_variety"
    assert recipe.maps_append[0].workshop_id == "3250132197"
    assert recipe.plugin_config["UseGameTimeLimit"] is False
    assert recipe.plugin_config["EnforceTimeLimit"] is True
    assert recipe.plugin_config["ChangeMapUse_host_workshop_map"] is True
    assert "wipe" not in recipe.id
    assert recipe.launch_upsert["-timeout"] == "120"


def test_launch_upsert_keeps_unrelated_flags():
    merged = upsert_additional_parameters("-insecure", KZ_RECIPE.launch_upsert)
    assert merged is not None
    assert "+sv_hibernate_when_empty 0" in merged
    assert "+host_workshop_map 3082213334" in merged
    assert "-timeout 120" in merged
    assert "-insecure" in merged


def test_launch_upsert_replaces_existing_workshop_map():
    merged = upsert_additional_parameters(
        "+host_workshop_map 3171881962 -nohltv",
        KZ_RECIPE.launch_upsert,
    )
    assert merged is not None
    assert "3171881962" not in merged
    assert "+host_workshop_map 3082213334" in merged
    assert "-nohltv" in merged


def test_addons_path_rejects_parent_segments():
    with pytest.raises(GameModeRemoteError):
        resolve_addons_directory("/home/cs2/../evil")
    path = resolve_addons_directory("/home/cs2server/cs2kz")
    assert path.endswith("/cs2/game/csgo/addons")
    assert path == "/home/cs2server/cs2kz/cs2/game/csgo/addons"


@pytest.mark.asyncio
async def test_wait_for_remote_files_succeeds_on_second_poll():
    calls = {"n": 0}

    class _Manager:
        async def execute_command(self, command, timeout=20):
            del command, timeout
            calls["n"] += 1
            if calls["n"] == 1:
                return True, "0=0\n1=1\n", ""
            return True, "0=1\n1=1\n", ""

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await wait_for_remote_files(
        _Manager(),
        ["/tmp/config.json", "/tmp/maps.txt"],
        timeout_seconds=30,
        interval_seconds=1,
        sleep=fake_sleep,
    )
    assert calls["n"] == 2
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_wait_for_remote_files_times_out():
    class _Manager:
        async def execute_command(self, command, timeout=20):
            del command, timeout
            return True, "0=0\n", ""

    async def no_sleep(_seconds: float) -> None:
        return None

    with pytest.raises(GameModeRemoteError, match="Timed out"):
        await wait_for_remote_files(
            _Manager(),
            ["/tmp/config.json"],
            timeout_seconds=0,
            interval_seconds=0,
            sleep=no_sleep,
        )


@pytest.mark.asyncio
async def test_catalog_skips_ssh_when_server_is_marked_down(monkeypatch):
    connect = AsyncMock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr("services.game_mode_install_service.connect", connect)
    monkeypatch.setattr(
        "services.game_mode_install_service.find_market_plugin_by_title",
        AsyncMock(return_value=None),
    )
    catalog = await catalog_for_server(
        object(),
        SimpleNamespace(
            id=1,
            game_directory="/home/cs2server/cs2kz",
            additional_parameters="-insecure",
            is_ssh_down=True,
        ),
    )
    assert catalog["reachable"] is False
    assert catalog["modes"][0]["id"] == "kz"
    assert catalog["modes"][0]["present"]["counterstrikesharp"] is None
    connect.assert_not_awaited()


def test_plan_hash_includes_wipe_flag():
    base = {"mode_id": "kz", "wipe_addons": False, "steps": ["install"]}
    wiped = {**base, "wipe_addons": True}
    assert _plan_hash(base) != _plan_hash(wiped)


@pytest.mark.asyncio
async def test_wipe_addons_rejects_non_addons_path():
    class _Manager:
        async def execute_command(self, command, timeout=20):
            raise AssertionError(f"should not wipe {command}")

    with pytest.raises(GameModeRemoteError, match="addons directory"):
        await wipe_addons_directory(_Manager(), "/tmp/not-addons")


@pytest.mark.asyncio
async def test_build_plan_lists_wipe_as_first_destructive_mutation(monkeypatch):
    from services.map_management_service import (
        DEFAULT_MAPS_CONFIG,
        DEFAULT_PLUGIN_CONFIG_CONTENT,
    )

    server = SimpleNamespace(
        id=1,
        game_directory="/home/cs2server/cs2kz",
        additional_parameters="-insecure",
    )
    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(
        "services.game_mode_install_service.connect",
        AsyncMock(return_value=manager),
    )
    # An unknown host release keeps the patchelf fix out of this plan.
    monkeypatch.setattr(
        "services.game_mode_install_service.read_linux_release",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.game_mode_install_service.inspect_game_mode_state",
        AsyncMock(
            return_value={
                "addons": True,
                "metamod": True,
                "css": True,
                "swiftly": False,
                "cs2kz": True,
                "mapchooser": True,
                "maps": True,
                "config": True,
            }
        ),
    )

    async def fake_read(*_args, **kwargs):
        if "maps.txt" in str(kwargs.get("label") or ""):
            return DEFAULT_MAPS_CONFIG
        return DEFAULT_PLUGIN_CONFIG_CONTENT

    monkeypatch.setattr(
        "services.game_mode_install_service._read_text",
        fake_read,
    )
    monkeypatch.setattr(
        "services.game_mode_install_service.find_market_plugin_by_title",
        AsyncMock(return_value=None),
    )

    wiped = await build_game_mode_plan(object(), server, "kz", wipe_addons=True)
    clean = await build_game_mode_plan(object(), server, "kz", wipe_addons=False)
    assert wiped["mutations"][0]["id"] == "wipe_addons"
    assert wiped["mutations"][0]["destructive"] is True
    assert wiped["addons_path"].endswith("/cs2/game/csgo/addons")
    assert any(item["id"] == "wipe_addons" for item in clean["mutations"]) is False
    assert wiped["plan_hash"] != clean["plan_hash"]
    assert "-insecure" in (wiped["startup"]["after"] or "")


@pytest.mark.asyncio
async def test_plan_adds_the_execstack_step_on_a_newer_glibc_host(monkeypatch):
    from services.map_management_service import (
        DEFAULT_MAPS_CONFIG,
        DEFAULT_PLUGIN_CONFIG_CONTENT,
    )

    server = SimpleNamespace(
        id=1,
        game_directory="/home/cs2server/cs2kz",
        additional_parameters="-insecure",
        os_id="debian",
        os_version="13",
        execstack_fix_targets=None,
        clear_execstack_override=None,
    )
    monkeypatch.setattr(
        "services.game_mode_install_service.connect",
        AsyncMock(return_value=SimpleNamespace(disconnect=AsyncMock())),
    )
    # The host record already matches, so no database write is attempted.
    monkeypatch.setattr(
        "services.game_mode_install_service.read_linux_release",
        AsyncMock(return_value=LinuxRelease("debian", "13")),
    )
    monkeypatch.setattr(
        "services.game_mode_install_service.inspect_game_mode_state",
        AsyncMock(
            return_value={
                "addons": True,
                "metamod": True,
                "css": True,
                "swiftly": False,
                "cs2kz": True,
                "mapchooser": True,
                "maps": True,
                "config": True,
            }
        ),
    )

    async def fake_read(*_args, **kwargs):
        if "maps.txt" in str(kwargs.get("label") or ""):
            return DEFAULT_MAPS_CONFIG
        return DEFAULT_PLUGIN_CONFIG_CONTENT

    monkeypatch.setattr("services.game_mode_install_service._read_text", fake_read)
    monkeypatch.setattr(
        "services.game_mode_install_service.find_market_plugin_by_title",
        AsyncMock(return_value=None),
    )

    plan = await build_game_mode_plan(object(), server, "kz", wipe_addons=True)

    assert plan["clear_execstack"] is True
    step = next(item for item in plan["steps"] if item["id"] == "clear_execstack")
    assert step["targets"] == list(DEFAULT_EXECSTACK_TARGETS)
    assert "--clear-execstack" in step["command"]
    # The fix is planned before the restart that loads the patched libraries.
    ids = [item["id"] for item in plan["steps"]]
    assert ids.index("clear_execstack") < ids.index("restart_and_wait")


@pytest.mark.asyncio
async def test_planned_execstack_step_runs_and_reports(monkeypatch):
    calls: list[tuple] = []
    reports: list[tuple[str, str]] = []

    async def fake_run(_manager, server, targets):
        calls.append((server, targets))
        return True, "cleared"

    async def report(step_id, step_status, message, metadata=None):
        reports.append((step_id, step_status))

    monkeypatch.setattr("services.game_mode_execstack.run_clear_execstack", fake_run)
    server = SimpleNamespace(id=1, game_directory="/srv/cs2")
    plan = {"steps": [{"id": "clear_execstack", "targets": ["a/b.so"]}]}

    await run_planned_execstack_step(plan, server, report)

    assert calls == [(server, ["a/b.so"])]
    assert reports == [("clear_execstack", "running"), ("clear_execstack", "completed")]


@pytest.mark.asyncio
async def test_planned_execstack_step_is_skipped_and_never_fails_the_install(monkeypatch):
    reports: list[tuple[str, str]] = []

    async def report(step_id, step_status, message, metadata=None):
        reports.append((step_id, step_status))

    monkeypatch.setattr(
        "services.game_mode_execstack.run_clear_execstack",
        AsyncMock(return_value=(False, "patchelf --clear-execstack is unavailable")),
    )
    server = SimpleNamespace(id=1, game_directory="/srv/cs2")

    # No planned step: nothing runs and nothing is reported.
    await run_planned_execstack_step({"steps": []}, server, report)
    assert reports == []

    await run_planned_execstack_step(
        {"steps": [{"id": "clear_execstack", "targets": None}]}, server, report
    )
    assert reports == [("clear_execstack", "running"), ("clear_execstack", "failed")]
