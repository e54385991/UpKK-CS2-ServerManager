"""Official SwiftlyS2 / Metamod ``gameinfo.gi`` SearchPaths rewriting."""

from __future__ import annotations

import pytest

from services.gameinfo_gi import (
    GAMEINFO_METAMOD_PATH,
    GAMEINFO_SWIFTLY_PATH,
    gameinfo_has_search_path,
    rewrite_gameinfo_search_paths,
)
from services.ssh.gameinfo import ensure_remote_gameinfo_search_paths, gameinfo_progress_detail
from tests.gameinfo_samples import (
    GAMEINFO_BARE,
    GAMEINFO_BOTH,
    GAMEINFO_LEFTOVER_VARIANTS,
    GAMEINFO_METAMOD,
    GAMEINFO_MIXED_INDENT,
    GAMEINFO_MIXED_INDENT_FIXED,
    GAMEINFO_SWIFTLY,
    GAMEINFO_SWIFTLY_THEN_METAMOD,
    GAMEINFO_VALVE_BOTH,
    GAMEINFO_VALVE_SEARCHPATHS,
)


def test_rewrite_adds_swiftly_after_low_violence():
    result = rewrite_gameinfo_search_paths(GAMEINFO_BARE, include_swiftly=True)
    assert result.changed
    assert result.content == GAMEINFO_SWIFTLY
    assert result.has_swiftly
    assert not result.has_metamod


def test_rewrite_adds_metamod_after_low_violence():
    result = rewrite_gameinfo_search_paths(GAMEINFO_BARE, include_metamod=True)
    assert result.content == GAMEINFO_METAMOD


def test_rewrite_keeps_existing_metamod_first_when_adding_swiftly():
    result = rewrite_gameinfo_search_paths(GAMEINFO_METAMOD, include_swiftly=True)
    assert result.content == GAMEINFO_BOTH
    assert result.has_metamod and result.has_swiftly


def test_rewrite_reorders_swiftly_above_metamod_to_official_order():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_SWIFTLY_THEN_METAMOD,
        include_metamod=True,
        include_swiftly=True,
    )
    assert result.content == GAMEINFO_BOTH
    metamod_at = result.content.index(GAMEINFO_METAMOD_PATH)
    swiftly_at = result.content.index(GAMEINFO_SWIFTLY_PATH)
    assert metamod_at < swiftly_at


def test_rewrite_is_idempotent_when_order_is_already_official():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_BOTH, include_metamod=True, include_swiftly=True
    )
    assert result.content == GAMEINFO_BOTH
    assert result.changed is False


def test_rewrite_preserves_existing_line_when_flag_omitted():
    result = rewrite_gameinfo_search_paths(GAMEINFO_SWIFTLY, include_metamod=True)
    assert result.content == GAMEINFO_BOTH


def test_rewrite_reports_missing_anchor():
    result = rewrite_gameinfo_search_paths("Game\tcsgo\n", include_swiftly=True)
    assert result.missing_anchor
    assert result.changed is False


def test_rewrite_matches_valve_stock_indent_and_separator():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_VALVE_SEARCHPATHS,
        include_metamod=True,
        include_swiftly=True,
    )
    assert result.content == GAMEINFO_VALVE_BOTH
    metamod_line = "\t\t\tGame\tcsgo/addons/metamod\n"
    swiftly_line = "\t\t\tGame\tcsgo/addons/swiftlys2\n"
    vanilla_line = "\t\t\tGame\tcsgo\n"
    assert metamod_line in result.content
    assert swiftly_line in result.content
    assert result.content.index(metamod_line) < result.content.index(swiftly_line)
    assert result.content.index(swiftly_line) < result.content.index(vanilla_line)


def test_rewrite_realigns_mixed_indent_leftovers_to_game_csgo():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_MIXED_INDENT,
        include_metamod=True,
        include_swiftly=True,
    )
    assert result.content == GAMEINFO_MIXED_INDENT_FIXED
    assert result.changed is True


def test_rewrite_consumes_quoted_and_gamebin_leftovers():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_LEFTOVER_VARIANTS,
        include_metamod=True,
        include_swiftly=True,
    )
    assert result.has_metamod and result.has_swiftly
    assert '"csgo/addons/metamod"' not in result.content
    assert "GameBin" not in result.content
    assert result.content.count(GAMEINFO_METAMOD_PATH) == 1
    assert result.content.count(GAMEINFO_SWIFTLY_PATH) == 1
    assert "\t\t\tGame\tcsgo/addons/metamod\n" in result.content
    assert "\t\t\tGame\tcsgo/addons/swiftlys2\n" in result.content


def test_gameinfo_has_search_path_ignores_plain_csgo_entry():
    assert gameinfo_has_search_path(GAMEINFO_BARE, GAMEINFO_METAMOD_PATH) is False
    assert gameinfo_has_search_path(GAMEINFO_METAMOD, GAMEINFO_METAMOD_PATH) is True


def test_progress_detail_lists_official_order():
    result = rewrite_gameinfo_search_paths(
        GAMEINFO_BOTH, include_metamod=True, include_swiftly=True
    )
    assert gameinfo_progress_detail(result) == (
        f"Game {GAMEINFO_METAMOD_PATH} then Game {GAMEINFO_SWIFTLY_PATH}"
    )
    assert GAMEINFO_METAMOD_PATH in gameinfo_progress_detail(None)


@pytest.mark.asyncio
async def test_remote_helper_writes_reordered_paths_and_backs_up():
    commands: list[str] = []
    written: list[str] = []

    async def execute(command: str, **_kwargs):
        commands.append(command)
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("test -d") and "swiftlys2" in command:
            return True, "exists", ""
        if command.startswith("cat "):
            return True, GAMEINFO_SWIFTLY_THEN_METAMOD, ""
        if command.startswith("cp "):
            return True, "", ""
        if "base64 -d" in command:
            written.append(command)
            return True, "", ""
        return False, "", command

    ok, message, result = await ensure_remote_gameinfo_search_paths(
        execute,
        "/srv/cs2/cs2/game/csgo/gameinfo.gi",
        include_metamod=True,
        detect_swiftly_dir="/srv/cs2/cs2/game/csgo/addons/swiftlys2",
    )
    assert ok and message == "updated"
    assert result is not None and result.content == GAMEINFO_BOTH
    assert any(command.startswith("cp ") for command in commands)
    assert written


@pytest.mark.asyncio
async def test_remote_helper_skips_write_when_already_configured():
    async def execute(command: str, **_kwargs):
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("cat "):
            return True, GAMEINFO_SWIFTLY, ""
        raise AssertionError(f"unexpected command: {command}")

    ok, message, result = await ensure_remote_gameinfo_search_paths(
        execute,
        "/srv/cs2/cs2/game/csgo/gameinfo.gi",
        include_swiftly=True,
    )
    assert ok and message == "already configured"
    assert result is not None and result.changed is False


@pytest.mark.asyncio
async def test_remote_helper_reports_read_and_anchor_failures():
    async def missing_cat(command: str, **_kwargs):
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("cat "):
            return False, "", "permission denied"
        raise AssertionError(command)

    ok, message, result = await ensure_remote_gameinfo_search_paths(
        missing_cat,
        "/srv/cs2/cs2/game/csgo/gameinfo.gi",
        include_swiftly=True,
    )
    assert not ok and result is None
    assert "Failed to read gameinfo.gi" in message

    async def no_anchor(command: str, **_kwargs):
        if command.startswith("test -f"):
            return True, "exists", ""
        if command.startswith("cat "):
            return True, "Game\tcsgo\n", ""
        raise AssertionError(command)

    ok, message, result = await ensure_remote_gameinfo_search_paths(
        no_anchor,
        "/srv/cs2/cs2/game/csgo/gameinfo.gi",
        include_swiftly=True,
    )
    assert not ok and result is not None and result.missing_anchor
    assert "Game_LowViolence" in message


@pytest.mark.asyncio
async def test_remote_helper_reports_missing_gameinfo():
    async def execute(command: str, **_kwargs):
        return False, "", "missing"

    ok, message, result = await ensure_remote_gameinfo_search_paths(
        execute,
        "/srv/cs2/cs2/game/csgo/gameinfo.gi",
        include_swiftly=True,
    )
    assert not ok and result is None
    assert "gameinfo.gi not found" in message
