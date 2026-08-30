"""Bounded capture regressions for long-running SSH command output."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.bounded_output import BoundedLineBuffer
from services.ssh_manager import SSHManager


def test_line_buffer_retains_a_bounded_utf8_tail():
    buffer = BoundedLineBuffer(96)

    for index in range(100):
        buffer.append(f"输出 line-{index}")

    output = buffer.text()
    assert buffer.truncated is True
    assert output.startswith(buffer.TRUNCATION_MARKER)
    assert "line-0\n" not in output
    assert "line-99" in output
    assert len(output.encode("utf-8")) <= 96 + len(buffer.TRUNCATION_MARKER.encode("utf-8")) + 1


def test_line_buffer_handles_one_oversized_unicode_line():
    buffer = BoundedLineBuffer(32)

    buffer.append(("前缀" * 100) + "tail")

    assert buffer.truncated is True
    assert buffer.text().endswith("tail")


class _Stream:
    def __init__(self, lines):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration from None


class _Process:
    def __init__(self):
        self.stdout = _Stream(f"stdout-{index}\n" for index in range(100))
        self.stderr = _Stream(f"stderr-{index}\n" for index in range(100))

    async def wait(self):
        return SimpleNamespace(exit_status=0)


class _Connection:
    async def create_process(self, _command, **_kwargs):
        return _Process()


@pytest.mark.asyncio
async def test_streaming_command_returns_bounded_stdout_and_stderr_tails():
    manager = SSHManager()
    manager.STREAMING_OUTPUT_MAX_BYTES = 128
    manager.conn = _Connection()

    success, stdout, stderr = await manager.execute_command_streaming("long-command")

    assert success is True
    assert "stdout-0\n" not in stdout
    assert "stderr-0\n" not in stderr
    assert stdout.endswith("stdout-99")
    assert stderr.endswith("stderr-99")
    assert BoundedLineBuffer.TRUNCATION_MARKER in stdout
    assert BoundedLineBuffer.TRUNCATION_MARKER in stderr
