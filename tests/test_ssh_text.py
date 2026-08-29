"""SSH/console text must survive CS2 panes that are not valid UTF-8."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.ssh.text import decode_remote_text, encode_console_input
from services.ssh_manager import SSHManager


def test_decode_remote_text_replaces_invalid_continuation_bytes():
    payload = b"A" * 2639 + b"\xef" + b"not-utf8"
    text = decode_remote_text(payload)
    assert text.startswith("A" * 2639)
    assert "\ufffd" in text
    assert "not-utf8" in text


def test_decode_remote_text_keeps_plain_strings():
    assert decode_remote_text("already text") == "already text"
    assert decode_remote_text(None) == ""


def test_encode_console_input_returns_utf8_bytes():
    assert encode_console_input("status") == b"status"


@pytest.mark.asyncio
async def test_create_interactive_process_requests_binary_pty():
    captured: dict = {}

    class _Connection:
        async def create_process(self, command=None, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return SimpleNamespace(stdout=None, stdin=None)

    manager = SSHManager()
    manager.conn = _Connection()
    await manager.create_interactive_process("tmux attach -t cs2server_1")
    assert captured["command"] == "tmux attach -t cs2server_1"
    assert captured["kwargs"]["encoding"] is None
    assert captured["kwargs"]["term_type"] == "xterm-256color"


@pytest.mark.asyncio
async def test_execute_command_does_not_raise_on_invalid_utf8():
    class _Connection:
        async def run(self, command, check=False, encoding=None):
            assert encoding is None
            assert command == "tmux capture-pane"
            return SimpleNamespace(
                stdout=b"ok\xefnot-utf8",
                stderr=b"",
                exit_status=0,
            )

    manager = SSHManager()
    manager.conn = _Connection()
    success, stdout, stderr = await manager.execute_command("tmux capture-pane")
    assert success is True
    assert stderr == ""
    assert "ok" in stdout
    assert "\ufffd" in stdout
    assert "not-utf8" in stdout
