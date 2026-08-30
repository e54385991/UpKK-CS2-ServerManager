"""Unit coverage for remote copy names and folder-upload path checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.file_manager.common import safe_relative_upload_path
from services.ssh_manager import SSHManager


def test_copy_collision_name_keeps_extension():
    assert SSHManager.copy_collision_name("server.cfg", 0) == "server.cfg"
    assert SSHManager.copy_collision_name("server.cfg", 1) == "server copy.cfg"
    assert SSHManager.copy_collision_name("server.cfg", 2) == "server copy 2.cfg"
    assert SSHManager.copy_collision_name("notes", 1) == "notes copy"


def test_safe_relative_upload_path_accepts_nested_folder():
    assert safe_relative_upload_path("cfg/server.cfg", "ignored.cfg") == "cfg/server.cfg"
    assert safe_relative_upload_path("", "server.cfg") == "server.cfg"
    assert safe_relative_upload_path("addons/css/plugin.json", None) == "addons/css/plugin.json"


@pytest.mark.parametrize(
    "relative",
    ["../etc/passwd", "/etc/passwd", "foo/../secret", "./../x"],
)
def test_safe_relative_upload_path_rejects_escape(relative: str):
    with pytest.raises(HTTPException) as captured:
        safe_relative_upload_path(relative, "x.cfg")
    assert captured.value.status_code == 422


@pytest.mark.asyncio
async def test_ensure_remote_parent_creates_nested_upload_dirs():
    manager = SSHManager.__new__(SSHManager)
    sftp = SimpleNamespace(makedirs=AsyncMock())
    ok, error = await manager._ensure_remote_parent("/tmp/cs2/plugin/cfg/server.cfg", sftp)
    assert ok is True
    assert error == ""
    sftp.makedirs.assert_awaited_once_with("/tmp/cs2/plugin/cfg", exist_ok=True)


@pytest.mark.asyncio
async def test_ensure_remote_parent_falls_back_to_mkdir():
    manager = SSHManager.__new__(SSHManager)
    sftp = SimpleNamespace(makedirs=AsyncMock(side_effect=OSError("sftp mkdir failed")))
    manager.execute_command = AsyncMock(return_value=(True, "", ""))
    ok, error = await manager._ensure_remote_parent("/tmp/cs2/plugin/cfg/server.cfg", sftp)
    assert ok is True
    assert error == ""
    manager.execute_command.assert_awaited_once()
    assert "mkdir -p --" in manager.execute_command.await_args.args[0]
