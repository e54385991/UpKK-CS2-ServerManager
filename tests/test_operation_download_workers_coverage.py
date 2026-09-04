"""Cover queued archive/download workers without network or SSH side effects."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.v1.operation_runner import downloads
from services.maintenance_lock import OperationBusyError
from services.server_operation_hub import ServerOperationConflict


class _Hub:
    def __init__(self, record=None):
        self.record = record or {
            "operation_id": "op-download",
            "server_id": 3,
            "actor_user_id": 9,
        }
        self.finished: list[tuple] = []
        self.patched: list[tuple] = []

    async def create(self, **kwargs):
        return {**self.record, **kwargs}

    async def get(self, _operation_id):
        return self.record

    async def mark_running(self, _operation_id):
        return self.record

    async def finish(self, operation_id, **kwargs):
        self.finished.append((operation_id, kwargs))

    async def patch(self, operation_id, **kwargs):
        self.patched.append((operation_id, kwargs))

    async def emit(self, *_args, **_kwargs):
        return None


class _Session:
    def __init__(self, user=None):
        self.user = user or SimpleNamespace(id=9, is_active=True, username="operator")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _item_id):
        return self.user


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _SSH:
    def __init__(self, *, connect=(True, "connected"), extract=(True, ""), download=None):
        self.connect_result = connect
        self.extract_result = extract
        self.download_results = list(download or [(True, "")])
        self.disconnected = False

    async def connect(self, _server):
        return self.connect_result

    async def disconnect(self):
        self.disconnected = True

    async def extract_archive(self, *_args, **_kwargs):
        return self.extract_result

    async def download_url_to_file(self, *_args, **_kwargs):
        return self.download_results.pop(0)


def _patch_worker(monkeypatch, module, *, hub=None, session=None, ssh=None, access=None):
    hub = hub or _Hub()
    session = session or _Session()
    ssh = ssh or _SSH()
    monkeypatch.setattr(module, "server_operation_hub", hub)
    monkeypatch.setattr(module, "async_session_maker", lambda: session)
    monkeypatch.setattr(module, "SSHManager", lambda: ssh)
    monkeypatch.setattr(
        module,
        "maintenance_lock_service",
        SimpleNamespace(get=lambda *_a, **_k: _Lock()),
    )
    monkeypatch.setattr(
        module,
        "require_server_access",
        AsyncMock(return_value=access or SimpleNamespace(id=3, game_directory="/srv/cs2")),
    )
    monkeypatch.setattr(module, "_audit_terminal", AsyncMock())
    return hub, ssh


@pytest.mark.asyncio
async def test_archive_worker_enqueue_and_success_failure_paths(monkeypatch):
    hub, ssh = _patch_worker(monkeypatch, downloads)
    dispatch = AsyncMock(side_effect=lambda record, _factory: record)
    monkeypatch.setattr(downloads, "_dispatch", dispatch)
    queued = await downloads.enqueue_extract_archive(
        server_id=3,
        actor_user_id=9,
        archive_path="/srv/cs2/a.zip",
        destination_path="/srv/cs2/addons",
        overwrite=True,
        source_folder="addons",
        strip_source_folder=True,
    )
    assert queued["action"] == "extract_archive"
    assert "--folder addons" in queued["command"]

    await downloads.run_extract_archive(
        operation_id="op-download",
        archive_path="/srv/cs2/a.zip",
        destination_path="/srv/cs2/addons",
        overwrite=True,
        source_folder=None,
        strip_source_folder=False,
    )
    assert hub.finished[-1][1]["success"] is True
    assert ssh.disconnected

    ssh.connect_result = (True, "connected")
    ssh.extract_result = (False, "bad archive")
    await downloads.run_extract_archive(
        operation_id="op-download",
        archive_path="/srv/cs2/a.zip",
        destination_path="/srv/cs2/addons",
        overwrite=False,
        source_folder=None,
        strip_source_folder=False,
    )
    assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_archive_worker_handles_missing_user_connection_and_operation_errors(monkeypatch):
    hub, _ssh = _patch_worker(
        monkeypatch,
        downloads,
        session=_Session(SimpleNamespace(id=9, is_active=False)),
    )
    await downloads.run_extract_archive(
        operation_id="op-download",
        archive_path="/srv/cs2/a.zip",
        destination_path="/srv/cs2/addons",
        overwrite=False,
        source_folder=None,
        strip_source_folder=False,
    )
    assert "no longer available" in hub.finished[-1][1]["message"]

    for exc in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        HTTPException(status_code=400, detail={"error": "bad"}),
        RuntimeError("unexpected"),
    ):
        hub, _ssh = _patch_worker(monkeypatch, downloads)
        monkeypatch.setattr(downloads, "require_server_access", AsyncMock(side_effect=exc))
        await downloads.run_extract_archive(
            operation_id="op-download",
            archive_path="/srv/cs2/a.zip",
            destination_path="/srv/cs2/addons",
            overwrite=False,
            source_folder=None,
            strip_source_folder=False,
        )
        assert hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_url_worker_normal_and_github_artifact_retry_paths(monkeypatch):
    hub, ssh = _patch_worker(
        monkeypatch,
        downloads,
        ssh=_SSH(download=[(True, "")]),
    )
    monkeypatch.setattr(downloads, "get_effective_github_token", AsyncMock(return_value="token"))
    common = __import__("api.routes.file_manager.common", fromlist=["remote_join"])
    monkeypatch.setattr(common, "_parse_github_actions_artifact_url", lambda _url: None)
    queued = await downloads.enqueue_url_download(
        server_id=3,
        actor_user_id=9,
        url="https://example.com/addon.zip",
        destination_path="/srv/cs2",
        target_path="/srv/cs2/addon.zip",
        overwrite=False,
    )
    assert queued["action"] == "download_url"
    await downloads.run_url_download(
        operation_id="op-download",
        url="https://example.com/addon.zip",
        destination_path="/srv/cs2",
        target_path="/srv/cs2/addon.zip",
        overwrite=False,
    )
    assert hub.finished[-1][1]["success"] is True

    ssh.download_results = [(False, "Download failed: transient"), (True, "")]
    monkeypatch.setattr(common, "_parse_github_actions_artifact_url", lambda _url: {"id": 1})
    monkeypatch.setattr(
        common,
        "_resolve_github_actions_artifact",
        AsyncMock(return_value=("https://cdn.example/addon.zip", "addon.zip")),
    )
    await downloads.run_url_download(
        operation_id="op-download",
        url="https://github.com/actions/artifact/1",
        destination_path="/srv/cs2",
        target_path=None,
        overwrite=True,
    )
    assert hub.patched and hub.finished[-1][1]["success"] is True


@pytest.mark.asyncio
async def test_url_worker_error_paths_are_terminal_and_disconnects(monkeypatch):
    for exc in (
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        HTTPException(status_code=403, detail="forbidden"),
        RuntimeError("network"),
    ):
        hub, ssh = _patch_worker(monkeypatch, downloads)
        monkeypatch.setattr(downloads, "require_server_access", AsyncMock(side_effect=exc))
        await downloads.run_url_download(
            operation_id="op-download",
            url="https://example.com/addon.zip",
            destination_path="/srv/cs2",
            target_path="/srv/cs2/addon.zip",
            overwrite=False,
        )
        assert hub.finished[-1][1]["success"] is False
        assert ssh.disconnected
