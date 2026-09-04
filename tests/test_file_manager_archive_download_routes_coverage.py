"""Isolated coverage for archive and URL-download route orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.file_manager import archives, downloads
from services.server_operation_hub import ServerOperationConflict


def _server(**overrides):
    values = {"id": 4, "game_directory": "/srv/cs2", "host": "host.test"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(**overrides):
    values = {"id": 9, "is_admin": False, "is_active": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _access(monkeypatch, module, server=None):
    row = server or _server()
    monkeypatch.setattr(module, "get_server_for_user", AsyncMock(return_value=row))
    return row


@pytest.mark.asyncio
async def test_inspect_archive_success_security_and_remote_failure(monkeypatch):
    server = _access(monkeypatch, archives)
    ssh = SimpleNamespace(
        inspect_archive=AsyncMock(
            return_value=(True, {"archive_type": "zip", "folders": ["cfg"], "entry_count": 2}, "")
        ),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(archives, "SSHManager", lambda: ssh)

    result = await archives.inspect_archive(
        4, archives.InspectArchiveRequest(archive_path="/srv/cs2/a.zip"), None, _user()
    )
    assert result == {"archive_type": "zip", "folders": ["cfg"], "entry_count": 2}
    ssh.inspect_archive.assert_awaited_once_with("/srv/cs2/a.zip", server)
    ssh.disconnect.assert_awaited_once()

    with pytest.raises(HTTPException) as caught:
        await archives.inspect_archive(
            4, archives.InspectArchiveRequest(archive_path="/etc/a.zip"), None, _user()
        )
    assert caught.value.status_code == 403

    ssh.inspect_archive.return_value = (False, {}, "bad archive")
    with pytest.raises(HTTPException) as caught:
        await archives.inspect_archive(
            4, archives.InspectArchiveRequest(archive_path="/srv/cs2/a.zip"), None, _user()
        )
    assert caught.value.status_code == 400
    assert ssh.disconnect.await_count == 2


@pytest.mark.asyncio
async def test_extract_archive_enqueues_normalized_options_and_conflicts(monkeypatch):
    server = _access(monkeypatch, archives)
    lock = AsyncMock()
    enqueue = AsyncMock(return_value={"operation_id": "op-1"})
    monkeypatch.setattr(archives, "reject_stuck_lock_unless_active", lock)
    monkeypatch.setattr(archives, "enqueue_extract_archive", enqueue)

    result = await archives.extract_archive(
        4,
        archives.ExtractArchiveRequest(
            archive_path="/srv/cs2/a.zip",
            destination_path="/srv/cs2/out/..",
            overwrite=True,
            source_folder="./pack/",
            strip_source_folder=True,
        ),
        None,
        _user(),
    )
    assert result == {
        "success": True,
        "task_id": "op-1",
        "message": "Extraction started",
        "status": "pending",
        "destination": "/srv/cs2",
    }
    lock.assert_awaited_once_with(4)
    enqueue.assert_awaited_once_with(
        server_id=4,
        actor_user_id=9,
        archive_path="/srv/cs2/a.zip",
        destination_path="/srv/cs2",
        overwrite=True,
        source_folder="pack",
        strip_source_folder=True,
    )

    enqueue.side_effect = ServerOperationConflict("busy", "op-current")
    with pytest.raises(HTTPException) as caught:
        await archives.extract_archive(
            4,
            archives.ExtractArchiveRequest(archive_path="/srv/cs2/a.zip"),
            None,
            _user(),
        )
    assert caught.value.status_code == 409

    with pytest.raises(HTTPException) as caught:
        await archives.extract_archive(
            4,
            archives.ExtractArchiveRequest(archive_path="/etc/a.zip"),
            None,
            _user(),
        )
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_archive_status_maps_hub_and_rejects_other_servers(monkeypatch):
    _access(monkeypatch, archives)
    monkeypatch.setattr(
        archives.server_operation_hub,
        "get",
        AsyncMock(
            side_effect=[
                {
                    "operation_id": "op-2",
                    "server_id": 4,
                    "status": "failed",
                    "message": "no space",
                    "destination_path": "/srv/cs2/out",
                    "archive_path": "/srv/cs2/a.zip",
                },
                None,
                {"operation_id": "op-3", "server_id": 5, "status": "queued"},
            ]
        ),
    )
    result = await archives.get_extraction_status(4, "op-2", None, _user())
    assert result["status"] == "failed"
    assert result["error"] == "no space"
    assert result["source_folder"] is None
    assert result["strip_source_folder"] is False
    for task in ("op-missing", "op-other"):
        with pytest.raises(HTTPException) as caught:
            await archives.get_extraction_status(4, task, None, _user())
        assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_url_download_success_validation_and_conflict(monkeypatch):
    server = _access(monkeypatch, downloads)
    validator = SimpleNamespace(
        validate_path_within_base=AsyncMock(return_value=(True, "")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(downloads, "SSHManager", lambda: validator)
    monkeypatch.setattr(downloads, "reject_stuck_lock_unless_active", AsyncMock())
    enqueue = AsyncMock(return_value={"operation_id": "dl-1"})
    monkeypatch.setattr(downloads, "enqueue_url_download", enqueue)

    result = await downloads.download_archive_from_url(
        4,
        downloads.DownloadUrlRequest(
            url="https://downloads.example/a.zip", destination_path="/srv/cs2/out"
        ),
        None,
        _user(),
    )
    assert result == {
        "success": True,
        "task_id": "dl-1",
        "status": "pending",
        "target_path": "/srv/cs2/out/a.zip",
    }
    enqueue.assert_awaited_once_with(
        server_id=4,
        actor_user_id=9,
        url="https://downloads.example/a.zip",
        destination_path="/srv/cs2/out",
        target_path="/srv/cs2/out/a.zip",
        overwrite=False,
    )
    validator.disconnect.assert_awaited_once()

    validator.validate_path_within_base.return_value = (False, "symlink escape")
    with pytest.raises(HTTPException) as caught:
        await downloads.download_archive_from_url(
            4,
            downloads.DownloadUrlRequest(
                url="https://downloads.example/a.zip", destination_path="/srv/cs2/out"
            ),
            None,
            _user(),
        )
    assert caught.value.status_code == 403

    enqueue.side_effect = ServerOperationConflict("busy")
    validator.validate_path_within_base.return_value = (True, "")
    with pytest.raises(HTTPException) as caught:
        await downloads.download_archive_from_url(
            4,
            downloads.DownloadUrlRequest(
                url="https://downloads.example/a.zip", destination_path="/srv/cs2/out"
            ),
            None,
            _user(),
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_url_download_rejects_bad_inputs_and_status(monkeypatch):
    _access(monkeypatch, downloads)
    for request in (
        downloads.DownloadUrlRequest(url="https://example.com/a.zip", destination_path=" "),
        downloads.DownloadUrlRequest(url="http://127.0.0.1/a.zip", destination_path="/srv/cs2"),
        downloads.DownloadUrlRequest(url="https://example.com/a.zip", destination_path="/etc"),
    ):
        with pytest.raises(HTTPException) as caught:
            await downloads.download_archive_from_url(4, request, None, _user())
        assert caught.value.status_code in (403, 422)

    monkeypatch.setattr(
        downloads.server_operation_hub,
        "get",
        AsyncMock(side_effect=[{"operation_id": "dl-2", "server_id": 4, "status": "running"}, None]),
    )
    result = await downloads.get_download_url_status(4, "dl-2", None, _user())
    assert result["status"] == "running"
    with pytest.raises(HTTPException) as caught:
        await downloads.get_download_url_status(4, "missing", None, _user())
    assert caught.value.status_code == 404
