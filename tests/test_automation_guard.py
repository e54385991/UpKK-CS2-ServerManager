"""Fresh A2S counts and conservative SSH evidence, without real game connections."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.automation import guard
from services.automation.commands import (
    AutomationDenied,
    CommandArgument,
    GameCommand,
    game_command,
)


@pytest.fixture
def target():
    return SimpleNamespace(
        id=12,
        host="HOST",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        manual_stop_requested=False,
    )


@pytest.fixture
def ssh():
    return SimpleNamespace(
        get_server_status=AsyncMock(return_value=(True, "stopped")),
        connect=AsyncMock(return_value=(True, "ok")),
        execute_command=AsyncMock(side_effect=[(True, "1 init /sbin/init\n", ""), (True, "", "")]),
        disconnect=AsyncMock(),
    )


@pytest.mark.parametrize("value", ["a", "A_09", "x" * 128])
def test_map_boundaries(value):
    assert game_command("css_wsmap").render([value]) == f"css_wsmap {value}"


@pytest.mark.parametrize(
    "seconds", ["1", "3600", "9999", "10000", "79999", "80000", "85999", "86000", "86399", "86400"]
)
def test_map_time_extension_boundaries(seconds):
    definition = game_command("css_ext_maptime")
    assert definition.render([seconds]) == f"css_ext_maptime {seconds}"
    assert definition.requires_online is True
    assert definition.requires_empty is False


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["0"],
        ["86401"],
        ["99999"],
        ["100000"],
        ["-1"],
        ["+3600"],
        ["03600"],
        ["3.5"],
        ["1e3"],
        ["３６００"],
        ["3600;quit"],
        ["3600\nquit"],
        ["3600", "extra"],
        [3600],
    ],
)
def test_map_time_extension_rejects_invalid_durations_and_injections(args):
    with pytest.raises(AutomationDenied):
        game_command("css_ext_maptime").render(args)


async def test_map_time_extension_allows_online_players_but_rejects_failed_queries(
    monkeypatch, target, ssh
):
    probe = AsyncMock(return_value=(True, {"player_count": 12, "bot_count": 2}))
    monkeypatch.setattr(guard.a2s_cache_service, "query_live_info", probe)
    definition = game_command("css_ext_maptime")
    await guard.ensure_eligible(
        target,
        ssh,
        requires_empty=definition.requires_empty,
        requires_online=definition.requires_online,
    )
    probe.return_value = (False, None)
    with pytest.raises(AutomationDenied, match="live A2S"):
        await guard.ensure_eligible(
            target,
            ssh,
            requires_empty=definition.requires_empty,
            requires_online=definition.requires_online,
        )
    ssh.connect.assert_not_awaited()


async def test_map_changes_allow_the_configured_human_occupancy(monkeypatch, target, ssh):
    probe = AsyncMock(return_value=(True, {"player_count": 1, "bot_count": 0}))
    monkeypatch.setattr(guard.a2s_cache_service, "query_live_info", probe)
    await guard.ensure_eligible(target, ssh, requires_empty=False, max_human_players=1)
    probe.return_value = (True, {"player_count": 2, "bot_count": 0})
    with pytest.raises(AutomationDenied, match="too many human players"):
        await guard.ensure_eligible(target, ssh, requires_empty=False, max_human_players=1)
    ssh.connect.assert_not_awaited()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["map", "extra"],
        [""],
        ["x" * 129],
        ["map;quit"],
        ["map\nquit"],
        ["map\tquit"],
        ["map\r"],
        ["map\x7f"],
        ["map\x00"],
        ["map/../x"],
        ['map"'],
        ["map&quit"],
        ["map|quit"],
        ["地图"],
        [False],
    ],
)
def test_map_and_argument_injection(args):
    with pytest.raises(AutomationDenied):
        game_command("css_wsmap").render(args)


def test_registry_requires_known_safe_name_and_validates_custom_arguments():
    with pytest.raises(AutomationDenied):
        game_command("quit")
    definition = GameCommand(
        "css_numeric", "Numeric command.", (CommandArgument("number", "Digits only.", "[0-9]+"),)
    )
    assert definition.render(["123"]) == "css_numeric 123"
    with pytest.raises(AutomationDenied):
        definition.render(["letters"])


@pytest.mark.parametrize(
    ("players", "bots", "allowed"),
    [
        (0, 0, True),
        (5, 5, True),
        (5, 4, False),
        (-1, 0, False),
        (0, 1, False),
        (256, 256, False),
        ("0", 0, False),
        (True, 0, False),
        (0, None, False),
    ],
)
async def test_human_and_bot_counts_are_verified(monkeypatch, target, ssh, players, bots, allowed):
    probe = AsyncMock(return_value=(True, {"player_count": players, "bot_count": bots}))
    monkeypatch.setattr(guard.a2s_cache_service, "query_live_info", probe)
    if allowed:
        await guard.ensure_eligible(target, ssh)
    else:
        with pytest.raises(AutomationDenied):
            await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    ssh.connect.assert_not_awaited()


async def test_manual_stop_blocks_before_any_probe(monkeypatch, target, ssh):
    probe = AsyncMock()
    monkeypatch.setattr(guard.a2s_cache_service, "query_live_info", probe)
    target.manual_stop_requested = True
    with pytest.raises(AutomationDenied, match="administrator"):
        await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    probe.assert_not_awaited()


async def test_only_restart_can_use_verified_offline_state(monkeypatch, target, ssh):
    monkeypatch.setattr(
        guard.a2s_cache_service, "query_live_info", AsyncMock(return_value=(False, None))
    )
    with pytest.raises(AutomationDenied, match="live A2S"):
        await guard.ensure_eligible(target, ssh)
    ssh.connect.assert_not_awaited()
    await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    assert ssh.get_server_status.await_count == 2
    assert [call.args[0] for call in ssh.execute_command.await_args_list] == [
        "ps -eo pid=,comm=,args=",
        "ss -H -lunt",
    ]
    ssh.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("processes", "ports"),
    [
        ((True, "15 cs2 /srv/cs2/cs2 -port 27015", ""), (True, "", "")),
        ((True, "15 cs2 /srv/cs2/cs2", ""), (True, "", "")),
        ((True, "15 bash /srv/cs2/cs2_autorestart.sh", ""), (True, "", "")),
        ((True, "bad-data", ""), (True, "", "")),
        ((False, "", "error"), (True, "", "")),
        ((True, "1 init /sbin/init", ""), (True, "udp UNCONN 0 0 0.0.0.0:27015 0.0.0.0:*", "")),
        ((True, "1 init /sbin/init", ""), (False, "", "error")),
        ((True, "1 init /sbin/init", ""), (True, "malformed", "")),
    ],
)
async def test_offline_ambiguity_fails_closed(monkeypatch, target, ssh, processes, ports):
    monkeypatch.setattr(
        guard.a2s_cache_service, "query_live_info", AsyncMock(return_value=(False, None))
    )
    ssh.execute_command.side_effect = [processes, ports]
    with pytest.raises(AutomationDenied):
        await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    ssh.disconnect.assert_awaited_once()


async def test_offline_session_changes_and_cancellation_cleanup(monkeypatch, target, ssh):
    monkeypatch.setattr(
        guard.a2s_cache_service, "query_live_info", AsyncMock(return_value=(False, None))
    )
    ssh.get_server_status.side_effect = [(True, "stopped"), (True, "running")]
    with pytest.raises(AutomationDenied, match="changed"):
        await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    ssh.get_server_status.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await guard.ensure_eligible(target, ssh, allow_stopped=True, requires_online=False)
    assert ssh.disconnect.await_count == 2
