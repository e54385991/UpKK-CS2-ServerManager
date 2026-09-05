"""Additional isolated coverage for file-manager security and workers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from api.routes.file_manager import common


def _server(**overrides):
    values = {"id": 4, "game_directory": "/srv/cs2"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _record(**overrides):
    values = {"operation_id": "op-1", "status": "queued"}
    values.update(overrides)
    return values


class _DbResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, user):
        self.user = user

    async def execute(self, _statement):
        return _DbResult(self.user)


@pytest.mark.asyncio
async def test_download_ticket_lifecycle_removes_expired_and_is_one_time(monkeypatch):
    common.download_tickets.clear()
    clock = iter((100.0, 100.0, 200.0, 200.0))
    monkeypatch.setattr(common.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(common.uuid, "uuid4", lambda: SimpleNamespace(hex="ticket-1"))

    common.download_tickets["expired"] = {
        "user_id": 8,
        "server_id": 4,
        "path": "/srv/cs2/old.zip",
        "expires_at": 99.0,
    }
    ticket = await common._create_download_ticket(7, 4, "/srv/cs2/a.zip")
    assert ticket == "ticket-1"
    assert "expired" not in common.download_tickets
    assert await common._consume_download_ticket(ticket, 4, "/srv/cs2/a.zip") == 7
    assert await common._consume_download_ticket(ticket, 4, "/srv/cs2/a.zip") is None

    monkeypatch.setattr(common.time, "monotonic", lambda: 100.0)
    common.download_tickets["wrong"] = {
        "user_id": 7,
        "server_id": 4,
        "path": "/srv/cs2/a.zip",
        "expires_at": 99.0,
    }
    assert await common._consume_download_ticket("wrong", 4, "/srv/cs2/a.zip") is None
    common.download_tickets["mismatch"] = {
        "user_id": 7,
        "server_id": 4,
        "path": "/srv/cs2/a.zip",
        "expires_at": 200.0,
    }
    assert await common._consume_download_ticket("mismatch", 5, "/srv/cs2/a.zip") is None


@pytest.mark.asyncio
async def test_download_authentication_covers_ticket_bearer_and_user_states(monkeypatch):
    active = SimpleNamespace(id=7, is_active=True)
    inactive = SimpleNamespace(id=7, is_active=False)
    monkeypatch.setattr(common, "_consume_download_ticket", AsyncMock(return_value=7))
    assert (
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket="ticket", db=_Db(active)
        )
        is active
    )

    monkeypatch.setattr(common, "_consume_download_ticket", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as caught:
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket="bad", db=_Db(active)
        )
    assert caught.value.status_code == 401

    monkeypatch.setattr(common.jwt, "decode", lambda *_args, **_kwargs: {"sub": "7"})
    monkeypatch.setattr(common, "_consume_download_ticket", AsyncMock())
    assert (
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket=None, authorization="Bearer token", db=_Db(active)
        )
        is active
    )
    for authorization in ("Basic token", "Bearer"):
        with pytest.raises(HTTPException) as caught:
            await common.get_current_active_user_for_download(
                4, "/srv/cs2/a.zip", ticket=None, authorization=authorization, db=_Db(active)
            )
        assert caught.value.status_code == 401

    for payload in ({}, {"sub": "bad"}):
        monkeypatch.setattr(
            common.jwt, "decode", lambda *_args, payload=payload, **_kwargs: payload
        )
        with pytest.raises(HTTPException) as caught:
            await common.get_current_active_user_for_download(
                4, "/srv/cs2/a.zip", ticket=None, authorization="Bearer token", db=_Db(active)
            )
        assert caught.value.status_code == 401

    monkeypatch.setattr(
        common.jwt,
        "decode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(common.InvalidTokenError("bad")),
    )
    with pytest.raises(HTTPException) as caught:
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket=None, authorization="Bearer token", db=_Db(active)
        )
    assert caught.value.status_code == 401

    monkeypatch.setattr(common.jwt, "decode", lambda *_args, **_kwargs: {"sub": "7"})
    with pytest.raises(HTTPException) as caught:
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket=None, authorization="Bearer token", db=_Db(None)
        )
    assert caught.value.status_code == 401
    with pytest.raises(HTTPException) as caught:
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket=None, authorization="Bearer token", db=_Db(inactive)
        )
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:
        await common.get_current_active_user_for_download(
            4, "/srv/cs2/a.zip", ticket=None, authorization=None, db=_Db(active)
        )
    assert caught.value.status_code == 401


def test_file_manager_path_and_payload_helpers_cover_rejections_and_mappings():
    assert common.resolve_extract_paths(_server(), "/srv/cs2/a.zip", "  ") == (
        "/srv/cs2/a.zip",
        "/srv/cs2",
    )
    for archive, destination in (("/etc/a.zip", "/srv/cs2"), ("/srv/cs2/a.zip", "/etc")):
        with pytest.raises(HTTPException) as caught:
            common.resolve_extract_paths(_server(), archive, destination)
        assert caught.value.status_code == 403

    assert common.file_task_payload_from_hub(
        _record(
            status="completed",
            message="done",
            destination_path="/srv/cs2/out",
            target_path="/srv/cs2/out/a.zip",
            archive_path="/srv/cs2/a.zip",
        )
    ) == {
        "task_id": "op-1",
        "status": "completed",
        "message": "done",
        "error": None,
        "target_path": "/srv/cs2/out/a.zip",
        "destination": "/srv/cs2/out",
        "destination_path": "/srv/cs2/out",
        "archive_path": "/srv/cs2/a.zip",
        "elapsed_seconds": None,
    }
    failed = common.file_task_payload_from_hub(
        _record(status="failed", message="secret-safe error")
    )
    assert failed["message"] is None
    assert failed["error"] == "secret-safe error"
    assert common.file_task_payload_from_hub(_record(status="unknown"))["status"] == "pending"


def test_github_error_mapping_and_archive_url_parsing_cover_all_statuses():
    assert "invalid" in str(common._github_artifact_http_error(401, "token", metadata=True)).lower()
    assert "configure" in str(common._github_artifact_http_error(403, None, metadata=False)).lower()
    assert "not found" in str(common._github_artifact_http_error(404, None, metadata=True)).lower()
    assert (
        "expired" in str(common._github_artifact_http_error(410, "token", metadata=False)).lower()
    )
    assert "500" in str(common._github_artifact_http_error(500, None, metadata=False))
    assert common._parse_github_actions_artifact_url(
        "https://github.com/a/b/actions/runs/1/artifacts/2"
    ) == (
        "a",
        "b",
        2,
    )


class _GithubClient:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _url, headers):
        del headers
        if self.error:
            raise self.error
        return self.responses.pop(0)


def _github_responses(metadata_response, redirect_response=None):
    base = "https://api.github.com/repos/a/b/actions/artifacts/2"
    return [
        httpx.Response(200, json=metadata_response, request=httpx.Request("GET", base)),
        httpx.Response(
            302,
            headers={"location": "https://cdn.example/a.zip"},
            request=httpx.Request("GET", f"{base}/zip"),
        )
        if redirect_response is None
        else redirect_response,
    ]


@pytest.mark.asyncio
async def test_github_artifact_resolution_covers_validation_errors(monkeypatch):
    artifact_url = "https://github.com/a/b/actions/runs/1/artifacts/2"

    async def resolve(responses=None, error=None):
        client = _GithubClient(responses, error)
        monkeypatch.setattr(common.httpx, "AsyncClient", lambda **_kwargs: client)
        return await common._resolve_github_actions_artifact(artifact_url, " ")

    for status_code in (401, 403, 404, 500):
        with pytest.raises(RuntimeError):
            await resolve([httpx.Response(status_code)])
    with pytest.raises(RuntimeError, match="invalid artifact metadata"):
        await resolve([httpx.Response(200, content=b"bad")])
    with pytest.raises(RuntimeError, match="expired"):
        await resolve([httpx.Response(200, json={"name": "x", "expired": True})])
    with pytest.raises(RuntimeError, match="valid name"):
        await resolve([httpx.Response(200, json={"name": ""})])
    with pytest.raises(RuntimeError, match="unsafe"):
        await resolve([httpx.Response(200, json={"name": "../bad"})])

    original_type = common.SSHManager.archive_type_from_path
    monkeypatch.setattr(
        common.SSHManager, "archive_type_from_path", classmethod(lambda _cls, _name: "tar")
    )
    with pytest.raises(RuntimeError, match="ZIP"):
        await resolve([httpx.Response(200, json={"name": "artifact.zip"})])
    monkeypatch.setattr(common.SSHManager, "archive_type_from_path", original_type)

    with pytest.raises(RuntimeError, match="request failed"):
        await resolve(_github_responses({"name": "artifact"}, httpx.Response(200)))
    with pytest.raises(RuntimeError, match="redirect"):
        await resolve(
            _github_responses(
                {"name": "artifact"},
                httpx.Response(302, request=httpx.Request("GET", "https://api.github.com")),
            )
        )
    with pytest.raises(RuntimeError, match="invalid artifact download"):
        await resolve(
            _github_responses(
                {"name": "artifact"},
                httpx.Response(
                    302,
                    headers={"location": "http://127.0.0.1/a.zip"},
                    request=httpx.Request("GET", "https://api.github.com/x"),
                ),
            )
        )
    with pytest.raises(RuntimeError, match="timed out"):
        await resolve(error=httpx.TimeoutException("timeout"))
    with pytest.raises(RuntimeError, match="connect"):
        await resolve(error=httpx.ConnectError("offline"))

    result = await resolve(_github_responses({"name": "artifact"}))
    assert result == ("https://cdn.example/a.zip", "artifact.zip")


class _TaskSSH:
    def __init__(self, *, connect=(True, "ok"), download=(True, ""), extract=(True, "")):
        self.connect_result = connect
        self.download_result = download
        self.extract_result = extract
        self.disconnect = AsyncMock()
        self.download_calls = []

    async def connect(self, server):
        del server
        return self.connect_result

    async def download_url_to_file(self, *args, **kwargs):
        self.download_calls.append((args, kwargs))
        return self.download_result

    async def extract_archive(self, *args, **kwargs):
        del args, kwargs
        return self.extract_result


@pytest.mark.asyncio
async def test_file_workers_cover_success_failure_retry_exception_and_cleanup(monkeypatch):
    common.download_url_tasks.clear()
    common.extraction_tasks.clear()
    common._download_url_task_refs.clear()
    common._extraction_task_refs.clear()
    server = _server()

    ssh = _TaskSSH()
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    common.download_url_tasks["download"] = {"created_at": 1.0}
    await common._run_download_url_task(
        "download", "https://example.com/a.zip", "/srv/cs2", None, server, False, None
    )
    assert common.download_url_tasks["download"]["status"] == "completed"
    assert ssh.disconnect.await_count == 1

    ssh = _TaskSSH(download=(False, "remote failed"))
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    common.download_url_tasks["failed"] = {"created_at": 1.0}
    await common._run_download_url_task(
        "failed", "https://example.com/a.zip", "/srv/cs2", "/srv/cs2/a.zip", server, False, None
    )
    assert common.download_url_tasks["failed"]["status"] == "failed"

    ssh = _TaskSSH(connect=(False, "offline"))
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    common.download_url_tasks["offline"] = {"created_at": 1.0}
    await common._run_download_url_task(
        "offline", "https://example.com/a.zip", "/srv/cs2", None, server, False, None
    )
    assert "Connection failed" in common.download_url_tasks["offline"]["error"]

    resolve = AsyncMock(
        side_effect=[
            ("https://cdn.example/a.zip", "a.zip"),
            ("https://cdn.example/a2.zip", "a.zip"),
        ]
    )
    monkeypatch.setattr(common, "_parse_github_actions_artifact_url", lambda _url: ("a", "b", 2))
    monkeypatch.setattr(common, "_resolve_github_actions_artifact", resolve)
    ssh = _TaskSSH(download=(False, "Download failed: expired"))
    ssh.download_url_to_file = AsyncMock(
        side_effect=[(False, "Download failed: expired"), (True, "")]
    )
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    common.download_url_tasks["github"] = {"created_at": 1.0}
    await common._run_download_url_task(
        "github",
        "https://github.com/a/b/actions/runs/1/artifacts/2",
        "/srv/cs2",
        None,
        server,
        True,
        "token",
    )
    assert common.download_url_tasks["github"]["status"] == "completed"
    assert ssh.download_url_to_file.await_count == 2
    assert resolve.await_count == 2

    ssh = _TaskSSH()
    ssh.disconnect.side_effect = RuntimeError("disconnect")
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    common.download_url_tasks["disconnect"] = {"created_at": 1.0}
    await common._run_download_url_task(
        "disconnect", "https://example.com/a.zip", "/srv/cs2", None, server, False, None
    )

    common.extraction_tasks["extract"] = {"created_at": 1.0}
    ssh = _TaskSSH(extract=(True, ""))
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    await common._run_extraction_task(
        "extract", "/srv/cs2/a.zip", "/srv/cs2/out", server, True, "addons", True
    )
    assert common.extraction_tasks["extract"]["status"] == "completed"

    common.extraction_tasks["extract-failed"] = {"created_at": 1.0}
    ssh = _TaskSSH(extract=(False, "bad archive"))
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    await common._run_extraction_task(
        "extract-failed", "/srv/cs2/a.zip", "/srv/cs2/out", server, False, None, False
    )
    assert common.extraction_tasks["extract-failed"]["status"] == "failed"

    common.extraction_tasks["extract-error"] = {"created_at": 1.0}
    ssh = _TaskSSH()
    ssh.extract_archive = AsyncMock(side_effect=RuntimeError("broken"))
    ssh.disconnect.side_effect = RuntimeError("disconnect")
    monkeypatch.setattr(common, "SSHManager", lambda: ssh)
    await common._run_extraction_task(
        "extract-error", "/srv/cs2/a.zip", "/srv/cs2/out", server, False, None, False
    )
    assert "broken" in common.extraction_tasks["extract-error"]["error"]

    common.download_url_tasks.update(
        {
            "old-done": {"completed_at": 1.0},
            "old-pending": {"created_at": 1.0},
            "fresh": {"created_at": 9999999999.0},
        }
    )
    common.extraction_tasks.update(
        {
            "old-done": {"completed_at": 1.0},
            "old-pending": {"created_at": 1.0},
            "fresh": {"created_at": 9999999999.0},
        }
    )
    monkeypatch.setattr(common.time, "time", lambda: 100000.0)
    await common._cleanup_old_download_url_tasks()
    await common._cleanup_old_extraction_tasks()
    assert "old-done" not in common.download_url_tasks
    assert "old-pending" not in common.extraction_tasks


@pytest.mark.asyncio
async def test_file_task_limiter_and_shutdown_wrapper(monkeypatch):
    called = []

    async def callback():
        called.append(True)

    await common._run_bounded_file_task(7, callback)
    assert called == [True]
    shutdown = AsyncMock()
    monkeypatch.setattr(common.file_task_registry, "shutdown", shutdown)
    common._download_url_task_refs["x"] = SimpleNamespace()
    common._extraction_task_refs["y"] = SimpleNamespace()
    await common.shutdown_background_tasks()
    shutdown.assert_awaited_once()
    assert not common._download_url_task_refs
    assert not common._extraction_task_refs
