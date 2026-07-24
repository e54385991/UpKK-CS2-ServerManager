"""Changed-line coverage for file-manager resource and cleanup boundaries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.requests import Request

from api.routes.file_manager import archives as archive_routes
from api.routes.file_manager import common as file_common
from api.routes.file_manager import downloads as download_routes
from api.routes.file_manager import files as file_routes
from modules.models import AuthType, Server


class _NoopLocks:
    @asynccontextmanager
    async def _hold(self):
        yield

    def get(self, *_args, **_kwargs):
        return self._hold()


class _FileSSH:
    def __init__(
        self,
        *,
        valid: bool = True,
        remote_success: bool = True,
        file_size: int | None = 10,
        disconnect_error: bool = False,
    ) -> None:
        self.valid = valid
        self.remote_success = remote_success
        self.file_size = file_size
        self.disconnect_error = disconnect_error
        self.disconnect_calls = 0
        self.download_path: str | None = None

    async def disconnect(self):
        self.disconnect_calls += 1
        if self.disconnect_error:
            raise ConnectionError("disconnect failed")

    async def list_directory(self, path, _server):
        return (
            True,
            [
                {
                    "name": "cfg",
                    "path": f"{path}/cfg",
                    "type": "directory",
                    "size": 0,
                    "modified": 1.0,
                    "permissions": "755",
                    "is_symlink": False,
                }
            ],
            "",
        )

    async def validate_path_within_base(self, *_args, **_kwargs):
        return self.valid, "symlink escaped"

    async def write_file(self, *_args):
        return self.remote_success, "write failed"

    async def upload_file(self, local_path, *_args):
        assert Path(local_path).is_file()
        return self.remote_success, "upload failed"

    async def create_directory(self, *_args):
        return self.remote_success, "mkdir failed"

    async def delete_path(self, *_args):
        return self.remote_success, "delete failed"

    async def rename_path(self, *_args):
        return self.remote_success, "rename failed"

    async def get_file_size(self, *_args):
        return True, self.file_size, ""

    async def stream_file(self, *_args):
        yield b"first"
        yield b"second"

    async def download_file(self, _remote_path, local_path, _server):
        self.download_path = local_path
        if self.remote_success:
            Path(local_path).write_bytes(b"download")
        return self.remote_success, "download failed"


class _TaskSSH:
    def __init__(
        self,
        *,
        connect_result: tuple[bool, str] = (True, "connected"),
        download_results: list[tuple[bool, str]] | None = None,
        cancel_first_disconnect: bool = False,
    ) -> None:
        self.connect_result = connect_result
        self.download_results = iter(download_results or [(True, "")])
        self.cancel_first_disconnect = cancel_first_disconnect
        self.disconnect_calls = 0
        self.download_calls: list[str] = []

    async def connect(self, _server):
        return self.connect_result

    async def download_url_to_file(
        self,
        url,
        _target_path,
        _server,
        **_kwargs,
    ):
        self.download_calls.append(url)
        return next(self.download_results)

    async def extract_archive(self, *_args, **_kwargs):
        return True, ""

    async def disconnect(self):
        self.disconnect_calls += 1
        if self.cancel_first_disconnect and self.disconnect_calls == 1:
            raise asyncio.CancelledError


class _Session:
    def __init__(self, server) -> None:
        self.server = server
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def get(self, _model, _server_id):
        return self.server

    async def commit(self):
        self.committed = True


def _server(**updates) -> Server:
    values = {
        "id": 11,
        "user_id": 7,
        "name": "coverage",
        "host": "server.example",
        "ssh_user": "cs2",
        "auth_type": AuthType.PASSWORD,
        "game_directory": "/srv/game",
    }
    values.update(updates)
    return Server(**values)


def _http_request(*, session_factory=object(), supervisor=None) -> Request:
    database = SimpleNamespace(session_factory=session_factory)
    container = SimpleNamespace(database=database)
    app = SimpleNamespace(
        state=SimpleNamespace(
            container=container,
            task_supervisor=supervisor,
        )
    )
    return Request({"type": "http", "app": app})


def _task_info() -> dict[str, object]:
    return {
        "status": "pending",
        "created_at": 1.0,
        "started_at": None,
        "completed_at": None,
        "message": None,
        "error": None,
    }


@pytest.fixture(autouse=True)
def _clear_file_task_state():
    file_common.download_url_tasks.clear()
    file_common._download_url_task_refs.clear()
    file_common.extraction_tasks.clear()
    file_common._extraction_task_refs.clear()
    yield
    file_common.download_url_tasks.clear()
    file_common._download_url_task_refs.clear()
    file_common.extraction_tasks.clear()
    file_common._extraction_task_refs.clear()


@pytest.mark.asyncio
async def test_common_task_ownership_snapshot_and_disconnect_boundaries(monkeypatch):
    observed: list[tuple[str, int]] = []

    async def callback(label: str, value: int):
        observed.append((label, value))

    await file_common._run_bounded_file_task(7, callback, "bounded", 1)
    assert observed == [("bounded", 1)]

    supervisor_task = object()
    supervisor = SimpleNamespace(create=Mock(return_value=supervisor_task))
    supervisor_request = _http_request(supervisor=supervisor)
    coroutine = callback("supervised", 2)
    assert (
        file_common._spawn_file_task(
            supervisor_request,
            coroutine,
            name="supervised-task",
        )
        is supervisor_task
    )
    coroutine.close()
    supervisor.create.assert_called_once()

    fallback_task = file_common._spawn_file_task(
        _http_request(supervisor=None),
        callback("fallback", 3),
        name="fallback-task",
    )
    await fallback_task
    await asyncio.sleep(0)
    assert ("fallback", 3) in observed

    owned_server = _server()
    success_session = _Session(owned_server)
    snapshot = await file_common._load_server_snapshot(
        lambda: success_session,
        11,
        7,
        False,
    )
    assert snapshot is not owned_server
    assert snapshot.id == owned_server.id
    assert success_session.committed is True

    for unavailable, user_id, is_admin in (
        (None, 7, False),
        (_server(user_id=8), 7, False),
    ):
        with pytest.raises(RuntimeError, match="no longer available"):
            await file_common._load_server_snapshot(
                lambda unavailable=unavailable: _Session(unavailable),
                11,
                user_id,
                is_admin,
            )

    failing_disconnect = _FileSSH(disconnect_error=True)
    await file_common._disconnect_ssh_manager(
        failing_disconnect,  # type: ignore[arg-type]
        operation="coverage",
    )
    assert failing_disconnect.disconnect_calls == 1


@pytest.mark.asyncio
async def test_ticket_incomplete_collision_and_timeout_fail_closed(monkeypatch):
    incomplete_request = _http_request()
    incomplete_request.app.state.container.redis = SimpleNamespace(client=SimpleNamespace(set=None))
    incomplete_request.app.state.container.settings = file_common.settings
    with pytest.raises(file_common.DownloadTicketStoreUnavailable):
        await file_common._create_download_ticket(incomplete_request, 7, 11, "/srv/game/file")

    class CollisionClient:
        calls = 0

        async def set(self, *_args, **_kwargs):
            self.calls += 1
            return False

    collision_client = CollisionClient()
    collision_request = _http_request()
    collision_request.app.state.container.redis = SimpleNamespace(client=collision_client)
    collision_request.app.state.container.settings = file_common.settings
    with pytest.raises(
        file_common.DownloadTicketStoreUnavailable,
        match="Unable to allocate a unique download ticket",
    ):
        await file_common._create_download_ticket(
            collision_request,
            7,
            11,
            "/srv/game/file",
        )
    assert collision_client.calls == 3

    class ImmediateTimeout:
        async def __aenter__(self):
            raise TimeoutError

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(file_common.asyncio, "timeout", lambda _seconds: ImmediateTimeout())
    with pytest.raises(
        file_common.DownloadTicketStoreUnavailable,
        match="temporarily unavailable",
    ):
        await file_common._create_download_ticket(
            collision_request,
            7,
            11,
            "/srv/game/file",
        )


@pytest.mark.asyncio
async def test_spawn_failures_remove_pending_archive_and_url_tasks(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=7, is_admin=False)
    task_uuid = UUID("12345678-1234-5678-1234-567812345678")
    request = _http_request(session_factory=object())

    monkeypatch.setattr(
        archive_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(
        archive_routes,
        "_cleanup_old_extraction_tasks",
        AsyncMock(),
    )
    monkeypatch.setattr(archive_routes.uuid, "uuid4", lambda: task_uuid)
    monkeypatch.setattr(
        archive_routes,
        "_spawn_file_task",
        Mock(side_effect=RuntimeError("supervisor closing")),
    )

    with pytest.raises(RuntimeError, match="supervisor closing"):
        await archive_routes.extract_archive(
            server_id=11,
            request=archive_routes.ExtractArchiveRequest(
                archive_path="/srv/game/archive.zip",
            ),
            http_request=request,
            db=object(),
            current_user=user,
        )
    assert str(task_uuid) not in file_common.extraction_tasks

    validator = _FileSSH()
    database = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(
        download_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(download_routes, "SSHManager", lambda: validator)
    monkeypatch.setattr(
        download_routes,
        "_cleanup_old_download_url_tasks",
        AsyncMock(),
    )
    monkeypatch.setattr(
        download_routes,
        "get_effective_github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(download_routes.uuid, "uuid4", lambda: task_uuid)
    monkeypatch.setattr(
        download_routes,
        "_spawn_file_task",
        Mock(side_effect=RuntimeError("supervisor closing")),
    )

    with pytest.raises(RuntimeError, match="supervisor closing"):
        await download_routes.download_archive_from_url(
            server_id=11,
            request=download_routes.DownloadUrlRequest(
                url="https://example.com/archive.zip",
                destination_path="/srv/game",
            ),
            http_request=request,
            db=database,
            current_user=user,
        )
    assert str(task_uuid) not in file_common.download_url_tasks
    assert validator.disconnect_calls == 1


@pytest.mark.asyncio
async def test_background_tasks_record_cancellation_errors_and_retry(monkeypatch):
    monkeypatch.setattr(file_common, "maintenance_lock_service", _NoopLocks())
    monkeypatch.setattr(
        file_common,
        "_load_server_snapshot",
        AsyncMock(return_value=_server()),
    )

    download_id = "cancel-download"
    file_common.download_url_tasks[download_id] = _task_info()
    file_common._download_url_task_refs[download_id] = object()
    cancelling_download = _TaskSSH(cancel_first_disconnect=True)
    monkeypatch.setattr(file_common, "SSHManager", lambda: cancelling_download)
    with pytest.raises(asyncio.CancelledError):
        await file_common._run_download_url_task(
            download_id,
            "https://example.com/archive.zip",
            "/srv/game",
            "/srv/game/archive.zip",
            11,
            7,
            False,
            False,
            None,
            object(),
        )
    assert file_common.download_url_tasks[download_id]["status"] == "failed"
    assert file_common.download_url_tasks[download_id]["error"] == "Download task was cancelled"
    assert cancelling_download.disconnect_calls == 2
    assert download_id not in file_common._download_url_task_refs

    extract_id = "cancel-extract"
    file_common.extraction_tasks[extract_id] = _task_info()
    file_common._extraction_task_refs[extract_id] = object()
    cancelling_extract = _TaskSSH(cancel_first_disconnect=True)
    monkeypatch.setattr(file_common, "SSHManager", lambda: cancelling_extract)
    with pytest.raises(asyncio.CancelledError):
        await file_common._run_extraction_task(
            extract_id,
            "/srv/game/archive.zip",
            "/srv/game/output",
            11,
            7,
            False,
            False,
            None,
            False,
            object(),
        )
    assert file_common.extraction_tasks[extract_id]["status"] == "failed"
    assert file_common.extraction_tasks[extract_id]["error"] == "Extraction task was cancelled"
    assert cancelling_extract.disconnect_calls == 2
    assert extract_id not in file_common._extraction_task_refs

    failed_extract_id = "failed-extract"
    file_common.extraction_tasks[failed_extract_id] = _task_info()
    file_common._extraction_task_refs[failed_extract_id] = object()
    file_common._load_server_snapshot.side_effect = RuntimeError("server disappeared")
    await file_common._run_extraction_task(
        failed_extract_id,
        "/srv/game/archive.zip",
        "/srv/game/output",
        11,
        7,
        False,
        False,
        None,
        False,
        object(),
    )
    assert file_common.extraction_tasks[failed_extract_id]["status"] == "failed"
    assert file_common.extraction_tasks[failed_extract_id]["error"] == "server disappeared"
    assert failed_extract_id not in file_common._extraction_task_refs

    file_common._load_server_snapshot.side_effect = None
    denied_id = "connect-denied"
    file_common.download_url_tasks[denied_id] = _task_info()
    denied_ssh = _TaskSSH(connect_result=(False, "denied"))
    monkeypatch.setattr(file_common, "SSHManager", lambda: denied_ssh)
    await file_common._run_download_url_task(
        denied_id,
        "https://example.com/archive.zip",
        "/srv/game",
        "/srv/game/archive.zip",
        11,
        7,
        False,
        False,
        None,
        object(),
    )
    assert file_common.download_url_tasks[denied_id]["error"] == "Connection failed: denied"
    assert denied_ssh.disconnect_calls == 1

    retry_id = "github-retry"
    artifact_url = "https://github.com/owner/repo/actions/runs/123/artifacts/456"
    file_common.download_url_tasks[retry_id] = _task_info()
    retry_ssh = _TaskSSH(
        download_results=[
            (False, "Download failed: signed URL expired"),
            (True, ""),
        ]
    )
    resolver = AsyncMock(
        side_effect=[
            ("https://objects.example/first", "archive.zip"),
            ("https://objects.example/second", "archive.zip"),
        ]
    )
    monkeypatch.setattr(file_common, "SSHManager", lambda: retry_ssh)
    monkeypatch.setattr(file_common, "_resolve_github_actions_artifact", resolver)
    await file_common._run_download_url_task(
        retry_id,
        artifact_url,
        "/srv/game",
        "/srv/game/archive.zip",
        11,
        7,
        False,
        False,
        "token",
        object(),
    )
    assert retry_ssh.download_calls == [
        "https://objects.example/first",
        "https://objects.example/second",
    ]
    assert file_common.download_url_tasks[retry_id]["status"] == "completed"


@pytest.mark.asyncio
async def test_file_mutations_always_disconnect_and_map_remote_errors(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(
        file_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(file_routes, "maintenance_lock_service", _NoopLocks())

    listing = _FileSSH(disconnect_error=True)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: listing)
    listed = await file_routes.list_directory(11, None, object(), user)
    assert listed.path == "/srv/game"
    assert listed.files[0].name == "cfg"
    assert listing.disconnect_calls == 1

    invalid_writer = _FileSSH(valid=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: invalid_writer)
    with pytest.raises(HTTPException) as validation_error:
        await file_routes.update_file_content(
            11,
            "/srv/game/server.cfg",
            file_routes.FileContentRequest(content="hostname coverage"),
            object(),
            user,
        )
    assert validation_error.value.status_code == 403
    assert invalid_writer.disconnect_calls == 1

    writer = _FileSSH(remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: writer)
    with pytest.raises(HTTPException) as write_error:
        await file_routes.update_file_content(
            11,
            "/srv/game/server.cfg",
            file_routes.FileContentRequest(content="hostname coverage"),
            object(),
            user,
        )
    assert write_error.value.status_code == 500
    assert writer.disconnect_calls == 1

    uploader = _FileSSH(remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: uploader)
    upload = UploadFile(file=BytesIO(b"archive"), filename="archive.zip")
    with pytest.raises(HTTPException) as upload_error:
        await file_routes.upload_file(11, "/srv/game", upload, object(), user)
    assert upload_error.value.status_code == 500
    assert uploader.disconnect_calls == 1

    creator = _FileSSH(remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: creator)
    with pytest.raises(HTTPException) as mkdir_error:
        await file_routes.create_directory(
            11,
            "/srv/game",
            file_routes.CreateDirectoryRequest(name="addons"),
            object(),
            user,
        )
    assert mkdir_error.value.status_code == 500
    assert creator.disconnect_calls == 1

    deleter = _FileSSH(remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: deleter)
    with pytest.raises(HTTPException) as delete_error:
        await file_routes.delete_path(11, "/srv/game/old.txt", object(), user)
    assert delete_error.value.status_code == 500
    assert deleter.disconnect_calls == 1

    renamer = _FileSSH(remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: renamer)
    with pytest.raises(HTTPException) as rename_error:
        await file_routes.rename_file_or_directory(
            11,
            "/srv/game",
            file_routes.RenameRequest(old_name="old.txt", new_name="new.txt"),
            object(),
            user,
        )
    assert rename_error.value.status_code == 500
    assert renamer.disconnect_calls == 1


@pytest.mark.asyncio
async def test_download_stream_small_file_and_failure_cleanup(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(
        file_routes,
        "get_server_for_user",
        AsyncMock(return_value=server),
    )

    streamer = _FileSSH(
        file_size=file_common.STREAMING_DOWNLOAD_THRESHOLD_BYTES + 1,
    )
    monkeypatch.setattr(file_routes, "SSHManager", lambda: streamer)
    streaming = await file_routes.download_file(
        11,
        "/srv/game/large.bin",
        object(),
        user,
    )
    assert isinstance(streaming, StreamingResponse)
    assert [chunk async for chunk in streaming.body_iterator] == [b"first", b"second"]
    assert streamer.disconnect_calls == 1
    assert streaming.background is not None
    await streaming.background()
    assert streamer.disconnect_calls == 1

    local = _FileSSH(file_size=10)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: local)
    downloaded = await file_routes.download_file(
        11,
        "/srv/game/small.bin",
        object(),
        user,
    )
    assert isinstance(downloaded, FileResponse)
    assert local.download_path is not None
    assert Path(local.download_path).is_file()
    assert local.disconnect_calls == 1
    assert downloaded.background is not None
    await downloaded.background()
    assert not Path(local.download_path).exists()

    failed = _FileSSH(file_size=10, remote_success=False)
    monkeypatch.setattr(file_routes, "SSHManager", lambda: failed)
    with pytest.raises(HTTPException) as failed_download:
        await file_routes.download_file(
            11,
            "/srv/game/fail.bin",
            object(),
            user,
        )
    assert failed_download.value.status_code == 500
    assert failed.download_path is not None
    assert not Path(failed.download_path).exists()
    assert failed.disconnect_calls == 1


@pytest.mark.asyncio
async def test_ticket_endpoint_maps_store_failure(monkeypatch):
    monkeypatch.setattr(
        file_routes,
        "get_server_for_user",
        AsyncMock(return_value=_server()),
    )
    monkeypatch.setattr(
        file_routes,
        "_create_download_ticket",
        AsyncMock(
            side_effect=file_routes.DownloadTicketStoreUnavailable(
                "Download ticket service is temporarily unavailable"
            )
        ),
    )

    with pytest.raises(HTTPException) as error:
        await file_routes.create_download_ticket(
            server_id=11,
            request=file_routes.DownloadTicketRequest(path="/srv/game/file.bin"),
            http_request=_http_request(),
            db=object(),
            current_user=SimpleNamespace(id=7),
        )
    assert error.value.status_code == 503
    assert error.value.detail == "Download ticket service is temporarily unavailable"
