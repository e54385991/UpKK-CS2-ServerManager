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


def test_incremental_console_does_not_replay_older_progress_from_history():
    previous = (
        "Waiting for user info...OK\n"
        "Update state (0x61) downloading, progress: 10.10 (7179405511 / 71089554542)\n"
        "Update state (0x61) downloading, progress: 10.17 (7232318229 / 71089554542)\n"
    )
    current = (
        "Waiting for user info...OK\n"
        "Update state (0x61) downloading, progress: 10.10 (7179405511 / 71089554542)\n"
        "Update state (0x61) downloading, progress: 10.17 (7232318229 / 71089554542)\n"
        "Update state (0x61) downloading, progress: 10.25 (7286981417 / 71089554542)\n"
    )
    assert incremental_console_lines(previous, current) == [
        "Update state (0x61) downloading, progress: 10.25 (7286981417 / 71089554542)"
    ]
    redraw = (
        "Waiting for user info...OK\n"
        "Update state (0x61) downloading, progress: 16.55 (11763884997 / 71089554542)\n"
    )
    next_redraw = (
        "Waiting for user info...OK\n"
        "Update state (0x61) downloading, progress: 16.62 (11814281445 / 71089554542)\n"
    )
    assert incremental_console_lines(redraw, next_redraw) == [
        "Update state (0x61) downloading, progress: 16.62 (11814281445 / 71089554542)"
    ]


def test_incremental_console_joins_wrapped_byte_tail():
    previous = "Update state (0x61) downloading, progress: 16.55 (11763884997 / 710895\n54542)\n"
    current = "Update state (0x61) downloading, progress: 16.62 (11814281445 / 710895\n54542)\n"
    assert incremental_console_lines(previous, current) == [
        "Update state (0x61) downloading, progress: 16.62 (11814281445 / 71089554542)"
    ]
    assert latest_console_heartbeat(current) == (
        "Update state (0x61) downloading, progress: 16.62 (11814281445 / 71089554542)"
    )
    assert incremental_console_lines(current, current) == []


def test_latest_progress_prefers_highest_bytes_over_old_complete_line():
    snapshot = (
        "Waiting for user info...OK\n"
        "Update state (0x61) downloading, progress: 10.17 (7232318229 / 71089554542)\n"
        "Update state (0x61) downloading, progress: 10.25 (7286981417 / 71089554542)\n"
        "Update state (0x61) downloading,\n"
        "progress: 50.64 (35999207698 / 71089554542)\n"
    )
    assert latest_console_heartbeat(snapshot).startswith(
        "Update state (0x61) downloading, progress: 50.64"
    )
    assert incremental_console_lines(
        "Update state (0x61) downloading, progress: 10.25 (7286981417 / 71089554542)\n",
        snapshot,
    ) == ["Update state (0x61) downloading, progress: 50.64 (35999207698 / 71089554542)"]


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
