"""Isolated coverage for the legacy file-manager endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.routes.file_manager import files


def _server(**overrides):
    values = {"id": 7, "game_directory": "/srv/cs2", "host": "example.test"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(**overrides):
    values = {"id": 3, "is_admin": False, "is_active": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _ssh(monkeypatch, **methods):
    manager = SimpleNamespace(disconnect=AsyncMock(), **methods)
    monkeypatch.setattr(files, "SSHManager", lambda: manager)
    return manager


def _access(monkeypatch, server=None):
    row = server or _server()
    monkeypatch.setattr(files, "get_server_for_user", AsyncMock(return_value=row))
    return row


def _audit(monkeypatch):
    event = AsyncMock()
    monkeypatch.setattr(files, "record_audit_event", event)
    return event


@pytest.mark.asyncio
async def test_list_directory_defaults_to_game_root_and_validates_error(monkeypatch):
    server = _access(monkeypatch)
    manager = _ssh(
        monkeypatch,
        list_directory=AsyncMock(
            return_value=(
                True,
                [
                    {
                        "name": "cfg",
                        "path": "/srv/cs2/cfg",
                        "type": "directory",
                        "size": 0,
                        "modified": 1.0,
                        "permissions": "755",
                        "is_symlink": False,
                    }
                ],
                "",
            )
        ),
    )

    result = await files.list_directory(7, None, db=None, current_user=_user())
    assert result.path == server.game_directory
    assert result.files[0].name == "cfg"
    manager.list_directory.assert_awaited_once_with("/srv/cs2", server)

    with pytest.raises(HTTPException) as caught:
        await files.list_directory(7, "/etc", db=None, current_user=_user())
    assert caught.value.status_code == 403

    manager.list_directory.return_value = (False, [], "offline")
    with pytest.raises(HTTPException) as caught:
        await files.list_directory(7, "/srv/cs2", db=None, current_user=_user())
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_file_content_read_and_update_cover_success_and_failures(monkeypatch):
    server = _access(monkeypatch)
    manager = _ssh(
        monkeypatch,
        read_file=AsyncMock(return_value=(True, "hostname test", "")),
        write_file=AsyncMock(return_value=(True, "")),
    )
    audit = _audit(monkeypatch)
    user = _user()

    result = await files.get_file_content(7, "/srv/cs2/server.cfg", db=None, current_user=user)
    assert result == {"path": "/srv/cs2/server.cfg", "content": "hostname test"}

    result = await files.update_file_content(
        7,
        "/srv/cs2/server.cfg",
        files.FileContentRequest(content="hostname next"),
        db=None,
        current_user=user,
        http_request=SimpleNamespace(),
    )
    assert result["success"] is True
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["details"]["bytes"] == len("hostname next".encode())

    manager.read_file.return_value = (False, "", "missing")
    with pytest.raises(HTTPException) as caught:
        await files.get_file_content(7, "/srv/cs2/nope", db=None, current_user=user)
    assert caught.value.status_code == 500

    manager.write_file.return_value = (False, "read-only")
    with pytest.raises(HTTPException) as caught:
        await files.update_file_content(
            7,
            "/srv/cs2/server.cfg",
            files.FileContentRequest(content="x"),
            db=None,
            current_user=user,
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 500

    with pytest.raises(HTTPException) as caught:
        await files.get_file_content(7, "/etc/passwd", db=None, current_user=user)
    assert caught.value.status_code == 403
    assert server.game_directory == "/srv/cs2"


class _Upload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self._chunks = iter(chunks)

    async def read(self, _size: int) -> bytes:
        return next(self._chunks, b"")


@pytest.mark.asyncio
async def test_upload_file_uses_tmp_path_and_cleans_up(monkeypatch, tmp_path):
    _access(monkeypatch)
    audit = _audit(monkeypatch)
    target = tmp_path / "upload.tmp"

    def mkstemp():
        return os.open(target, os.O_CREAT | os.O_RDWR), str(target)

    monkeypatch.setattr(files.tempfile, "mkstemp", mkstemp)
    manager = _ssh(monkeypatch, upload_file=AsyncMock(return_value=(True, "")))
    result = await files.upload_file(
        7,
        "/srv/cs2",
        file=_Upload("server.cfg", [b"hostname ", b"test", b""]),
        relative_path="cfg/server.cfg",
        db=None,
        current_user=_user(),
        http_request=SimpleNamespace(),
    )
    assert result["path"] == "/srv/cs2/cfg/server.cfg"
    assert result["filename"] == "server.cfg"
    assert target.exists() is False
    manager.upload_file.assert_awaited_once()
    assert manager.upload_file.await_args.args[0] == str(target)
    assert manager.upload_file.await_args.args[1] == "/srv/cs2/cfg/server.cfg"
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_file_rejects_escape_oversize_and_remote_failure(monkeypatch, tmp_path):
    _access(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        await files.upload_file(
            7,
            "/srv/cs2",
            file=_Upload("x.cfg", [b""]),
            relative_path="../escape/x.cfg",
            db=None,
            current_user=_user(),
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 422

    target = tmp_path / "large.tmp"
    monkeypatch.setattr(
        files.tempfile,
        "mkstemp",
        lambda: (os.open(target, os.O_CREAT | os.O_RDWR), str(target)),
    )
    monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 3)
    _ssh(monkeypatch, upload_file=AsyncMock(return_value=(True, "")))
    with pytest.raises(HTTPException) as caught:
        await files.upload_file(
            7,
            "/srv/cs2",
            file=_Upload("large.bin", [b"1234", b""]),
            relative_path=None,
            db=None,
            current_user=_user(),
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 413
    assert target.exists() is False

    monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 4 * 1024 * 1024 * 1024)
    target = tmp_path / "failed.tmp"
    monkeypatch.setattr(
        files.tempfile,
        "mkstemp",
        lambda: (os.open(target, os.O_CREAT | os.O_RDWR), str(target)),
    )
    manager = _ssh(monkeypatch, upload_file=AsyncMock(return_value=(False, "no space")))
    with pytest.raises(HTTPException) as caught:
        await files.upload_file(
            7,
            "/srv/cs2",
            file=_Upload("failed.bin", [b"x", b""]),
            relative_path=None,
            db=None,
            current_user=_user(),
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 500
    manager.disconnect.assert_awaited_once()
    assert target.exists() is False


@pytest.mark.asyncio
async def test_download_file_streams_large_files_and_disconnects(monkeypatch):
    server = _access(monkeypatch)

    async def chunks(_path, _server):
        yield b"part-1"
        yield b"part-2"

    manager = _ssh(
        monkeypatch,
        get_file_size=AsyncMock(
            return_value=(True, files.STREAMING_DOWNLOAD_THRESHOLD_BYTES + 1, "")
        ),
        stream_file=chunks,
    )
    response = await files.download_file(7, "/srv/cs2/big.zip", db=None, current_user=_user())
    assert isinstance(response, StreamingResponse)
    assert await response.body_iterator.__anext__() == b"part-1"
    assert await response.body_iterator.__anext__() == b"part-2"
    with pytest.raises(StopAsyncIteration):
        await response.body_iterator.__anext__()
    manager.get_file_size.assert_awaited_once_with("/srv/cs2/big.zip", server)
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_file_returns_file_response_and_handles_errors(monkeypatch, tmp_path):
    _access(monkeypatch)
    target = tmp_path / "download.tmp"
    target.write_bytes(b"archive")
    monkeypatch.setattr(
        files.tempfile,
        "mkstemp",
        lambda: (os.open(target, os.O_CREAT | os.O_RDWR), str(target)),
    )
    manager = _ssh(
        monkeypatch,
        get_file_size=AsyncMock(return_value=(True, 7, "")),
        download_file=AsyncMock(return_value=(True, "")),
    )
    response = await files.download_file(7, "/srv/cs2/archive.zip", db=None, current_user=_user())
    assert isinstance(response, FileResponse)
    assert response.filename == "archive.zip"
    manager.disconnect.assert_awaited_once()

    manager = _ssh(
        monkeypatch,
        get_file_size=AsyncMock(return_value=(False, None, "stat failed")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.download_file(7, "/srv/cs2/archive.zip", db=None, current_user=_user())
    assert caught.value.status_code == 500
    manager.disconnect.assert_awaited_once()

    target.write_bytes(b"archive")
    manager = _ssh(
        monkeypatch,
        get_file_size=AsyncMock(return_value=(True, 7, "")),
        download_file=AsyncMock(return_value=(False, "download failed")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.download_file(7, "/srv/cs2/archive.zip", db=None, current_user=_user())
    assert caught.value.status_code == 500
    manager.disconnect.assert_awaited_once()

    manager = _ssh(
        monkeypatch,
        get_file_size=AsyncMock(side_effect=RuntimeError("unexpected")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.download_file(7, "/srv/cs2/archive.zip", db=None, current_user=_user())
    assert caught.value.status_code == 500
    manager.disconnect.assert_awaited_once()

    with pytest.raises(HTTPException) as caught:
        await files.download_file(7, "/etc/passwd", db=None, current_user=_user())
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_download_ticket_endpoint_binds_path(monkeypatch):
    _access(monkeypatch)
    monkeypatch.setattr(files, "_create_download_ticket", AsyncMock(return_value="ticket-1"))
    result = await files.create_download_ticket(
        7,
        files.DownloadTicketRequest(path="/srv/cs2/a.zip"),
        db=None,
        current_user=_user(),
    )
    assert result == {"ticket": "ticket-1", "expires_in": files.DOWNLOAD_TICKET_TTL_SECONDS}
    with pytest.raises(HTTPException) as caught:
        await files.create_download_ticket(
            7,
            files.DownloadTicketRequest(path="/etc/passwd"),
            db=None,
            current_user=_user(),
        )
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_directory_mutations_cover_security_and_ssh_results(monkeypatch):
    _access(monkeypatch)
    audit = _audit(monkeypatch)
    user = _user()
    manager = _ssh(
        monkeypatch,
        create_directory=AsyncMock(return_value=(True, "")),
        delete_path=AsyncMock(return_value=(True, "")),
        rename_path=AsyncMock(return_value=(True, "")),
    )

    mkdir = await files.create_directory(
        7,
        "/srv/cs2",
        files.CreateDirectoryRequest(name="new"),
        db=None,
        current_user=user,
        http_request=SimpleNamespace(),
    )
    assert mkdir["path"] == "/srv/cs2/new"
    deleted = await files.delete_path(
        7, "/srv/cs2/old", db=None, current_user=user, http_request=SimpleNamespace()
    )
    assert deleted["success"] is True
    renamed = await files.rename_file_or_directory(
        7,
        "/srv/cs2",
        files.RenameRequest(old_name="old", new_name="new"),
        db=None,
        current_user=user,
        http_request=SimpleNamespace(),
    )
    assert renamed["new_path"] == "/srv/cs2/new"
    assert manager.disconnect.await_count == 1
    assert audit.await_count == 3

    manager.create_directory.return_value = (False, "mkdir failed")
    with pytest.raises(HTTPException) as caught:
        await files.create_directory(
            7,
            "/srv/cs2",
            files.CreateDirectoryRequest(name="again"),
            db=None,
            current_user=user,
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 500
    manager.delete_path.return_value = (False, "delete failed")
    with pytest.raises(HTTPException) as caught:
        await files.delete_path(
            7, "/srv/cs2/old", db=None, current_user=user, http_request=SimpleNamespace()
        )
    assert caught.value.status_code == 500
    manager.rename_path.return_value = (False, "rename failed")
    with pytest.raises(HTTPException) as caught:
        await files.rename_file_or_directory(
            7,
            "/srv/cs2",
            files.RenameRequest(old_name="old", new_name="new"),
            db=None,
            current_user=user,
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 500

    for operation in (
        lambda: files.create_directory(
            7,
            "/srv/cs2",
            files.CreateDirectoryRequest(name="../escape"),
            db=None,
            current_user=user,
            http_request=SimpleNamespace(),
        ),
        lambda: files.delete_path(
            7, "/srv/cs2", db=None, current_user=user, http_request=SimpleNamespace()
        ),
        lambda: files.rename_file_or_directory(
            7,
            "/srv/cs2",
            files.RenameRequest(old_name=".", new_name="new"),
            db=None,
            current_user=user,
            http_request=SimpleNamespace(),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await operation()
        assert caught.value.status_code == 403 or caught.value.status_code == 422


@pytest.mark.asyncio
async def test_copy_paths_validates_each_source_and_disconnects(monkeypatch):
    _access(monkeypatch)
    audit = _audit(monkeypatch)
    manager = _ssh(
        monkeypatch,
        copy_into_directory=AsyncMock(
            side_effect=lambda source, destination, _server: (
                True,
                f"{destination}/{Path(source).name}",
                "",
            )
        ),
    )
    result = await files.copy_paths(
        7,
        files.CopyPathsRequest(
            sources=["/srv/cs2/a.cfg", "", "/srv/cs2/b.cfg"],
            destination="/srv/cs2/cfg",
        ),
        db=None,
        current_user=_user(),
        http_request=SimpleNamespace(),
    )
    assert result["paths"] == ["/srv/cs2/cfg/a.cfg", "/srv/cs2/cfg/b.cfg"]
    assert manager.disconnect.await_count == 1
    audit.assert_awaited_once()

    invalid_requests = (
        files.CopyPathsRequest(sources=["/srv/cs2/a"], destination=""),
        files.CopyPathsRequest(sources=[], destination="/srv/cs2"),
        files.CopyPathsRequest(sources=["/etc/passwd"], destination="/srv/cs2"),
        files.CopyPathsRequest(sources=["/srv/cs2/a"], destination="/etc"),
    )
    for request in invalid_requests:
        with pytest.raises(HTTPException) as caught:
            await files.copy_paths(
                7,
                request,
                db=None,
                current_user=_user(),
                http_request=SimpleNamespace(),
            )
        assert caught.value.status_code in (403, 422)

    manager = _ssh(
        monkeypatch,
        copy_into_directory=AsyncMock(return_value=(False, "", "copy failed")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.copy_paths(
            7,
            files.CopyPathsRequest(sources=["/srv/cs2/a"], destination="/srv/cs2"),
            db=None,
            current_user=_user(),
            http_request=SimpleNamespace(),
        )
    assert caught.value.status_code == 500
    manager.disconnect.assert_awaited_once()
