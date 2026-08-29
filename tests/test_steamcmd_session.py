"""SteamCMD must run in a detached tmux/screen session, not on the SSH TTY."""

from __future__ import annotations

from services.game_session import (
    TMUX_SOCKET_NAME,
    start_session_command,
    steamcmd_session_name,
)
from services.steamcmd_session import (
    incremental_console_lines,
    latest_console_heartbeat,
    parse_steamcmd_exit_code,
    steamcmd_exit_path,
    wrap_steamcmd_payload,
)


def test_steamcmd_session_name_is_not_the_game_session():
    assert steamcmd_session_name(2) == "cs2steamcmd_2"
    assert steamcmd_session_name(2) != "cs2server_2"


def test_wrap_steamcmd_payload_records_exit_code():
    payload = wrap_steamcmd_payload(
        "cd /tmp/cs2-lan-ops/steamcmd && ./steamcmd.sh +quit",
        "/tmp/cs2-lan-ops/.upkk-steamcmd-exit",
    )
    start = start_session_command("tmux", "cs2steamcmd_2", payload)
    assert start.startswith(f"tmux -L {TMUX_SOCKET_NAME} -f /dev/null new-session -d")
    assert "cs2steamcmd_2" in start
    assert "steamcmd.sh" in start
    assert ".upkk-steamcmd-exit" in start
    assert "bash -lc" in start


def test_steamcmd_exit_path_stays_under_the_game_directory():
    assert steamcmd_exit_path("/tmp/cs2-lan-ops") == "/tmp/cs2-lan-ops/.upkk-steamcmd-exit"
    assert steamcmd_exit_path("/tmp/cs2-lan-ops/") == "/tmp/cs2-lan-ops/.upkk-steamcmd-exit"


def test_incremental_console_appends_new_suffix_lines():
    assert incremental_console_lines("a\n", "a\nb\n") == ["b"]
    assert incremental_console_lines("same", "same") == []


def test_incremental_console_emits_latest_progress_on_redraw():
    assert incremental_console_lines(
        "Update state (0x61) downloading, progress: 2.07",
        "Update state (0x61) downloading, progress: 2.20",
    ) == ["Update state (0x61) downloading, progress: 2.20"]


def test_latest_console_heartbeat_keeps_cr_only_progress():
    snapshot = "Update state (0x61) downloading, progress: 9.10\r"
    assert latest_console_heartbeat(snapshot) == ("Update state (0x61) downloading, progress: 9.10")
    assert incremental_console_lines("", snapshot) == [
        "Update state (0x61) downloading, progress: 9.10"
    ]


def test_parse_steamcmd_exit_code():
    assert parse_steamcmd_exit_code("0\n") == 0
    assert parse_steamcmd_exit_code("8\n") == 8
    assert parse_steamcmd_exit_code("") is None
    assert parse_steamcmd_exit_code("not-a-code") is None
