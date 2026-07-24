"""Focused regression coverage for S3 client leases and backup operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from modules.models import AuthType, Server, User
from services.s3_backup_service import S3BackupService


def make_user(
    user_id: int | None = 1,
    *,
    enabled: bool = True,
    retention_count: int = 2,
) -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        hashed_password="hash",
        s3_enabled=enabled,
        s3_endpoint_url="https://s3.example.com",
        s3_region="test-1",
        s3_bucket="backups",
        s3_access_key_id="access-key",
        s3_secret_access_key="secret-key",
        s3_prefix="cs2",
        s3_use_ssl=True,
        s3_retention_count=retention_count,
    )


def make_server() -> Server:
    return Server(
        id=7,
        user_id=1,
        name="Dust Hub",
        host="192.0.2.10",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        game_port=27015,
    )


class MemoryBody:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content


class RecordingS3Client:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        mismatched_download: bool = False,
        delete_errors: list[dict[str, str]] | None = None,
    ) -> None:
        self.fail_at = fail_at
        self.mismatched_download = mismatched_download
        self.delete_errors = delete_errors
        self.payload = b""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.close_calls = 0

    def _record(self, operation: str, **kwargs: Any) -> None:
        self.calls.append((operation, kwargs))
        if self.fail_at == operation:
            raise RuntimeError(f"{operation} denied")

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list", **kwargs)
        return {"Contents": []}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("upload", **kwargs)
        self.payload = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, MemoryBody]:
        self._record("download", **kwargs)
        content = b"wrong payload" if self.mismatched_download else self.payload
        return {"Body": MemoryBody(content)}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete", **kwargs)
        return {}

    def upload_file(self, local_path: str, bucket: str, key: str) -> None:
        self._record("upload_file", local_path=local_path, bucket=bucket, key=key)
        assert Path(local_path).read_bytes() == b"archive"

    def download_file(self, bucket: str, key: str, local_path: str) -> None:
        self._record("download_file", bucket=bucket, key=key, local_path=local_path)
        Path(local_path).write_bytes(b"restored archive")

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_objects", **kwargs)
        return {"Errors": self.delete_errors or []}

    def close(self) -> None:
        self.close_calls += 1


def install_client(monkeypatch: pytest.MonkeyPatch, service: S3BackupService, client: Any) -> None:
    monkeypatch.setattr(service, "_get_client", lambda _user: client)


def test_configuration_validation_and_cache_size_guard() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        S3BackupService(max_cached_clients=0)

    service = S3BackupService()
    user = make_user()
    assert service.is_configured(user) is True

    user.s3_enabled = False
    assert service.is_configured(user) is False
    user.s3_enabled = True
    user.s3_secret_access_key = None
    assert service.is_configured(user) is False


@pytest.mark.asyncio
async def test_transient_user_client_is_not_cached_and_release_is_idempotent(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    install_client(monkeypatch, service, client)

    entry, clients_to_close = service._acquire_cached_client(make_user(None))

    assert clients_to_close == []
    assert entry.active_leases == 1
    assert entry.retired is True
    assert service.cached_client_count == 0
    assert service._release_cached_client(entry) is client
    assert service._release_cached_client(entry) is None
    await service._close_clients([client])
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_close_clients_deduplicates_and_supports_async_and_failed_closers(caplog) -> None:
    service = S3BackupService()
    regular = RecordingS3Client()

    class NoClose:
        pass

    class AsyncClose:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    class FailedClose:
        def close(self) -> None:
            raise RuntimeError("endpoint=https://secret.example credential=do-not-log")

    async_client = AsyncClose()
    with caplog.at_level(logging.WARNING, logger="services.s3_backup_service"):
        await service._close_clients([regular, regular, NoClose(), async_client, FailedClose()])

    assert regular.close_calls == 1
    assert async_client.close_calls == 1
    assert "RuntimeError" in caplog.text
    assert "secret.example" not in caplog.text
    assert "do-not-log" not in caplog.text


@pytest.mark.asyncio
async def test_invalidate_none_is_noop_and_idle_client_is_closed(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    install_client(monkeypatch, service, client)
    user = make_user(23)

    assert await service.invalidate_user(None) == 0
    async with service._client_lease(user) as leased:
        assert leased is client

    assert await service.invalidate_user(user.id) == 1
    assert service.cached_client_count == 0
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_connection_probe_succeeds_and_reuses_one_leased_client(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    install_client(monkeypatch, service, client)

    success, message, steps = await service.test_connection(make_user())

    assert success is True
    assert "list, upload, download, and delete all passed" in message
    assert [(step["name"], step["status"]) for step in steps] == [
        ("list", "success"),
        ("upload", "success"),
        ("download", "success"),
        ("delete", "success"),
    ]
    assert [operation for operation, _kwargs in client.calls] == [
        "list",
        "upload",
        "download",
        "delete",
    ]
    await service.close()
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("fail_at", "expected_message", "expected_steps"),
    [
        ("list", "S3 list/read test failed: list denied", [("list", "failed")]),
        (
            "upload",
            "S3 upload test failed: upload denied",
            [("list", "success"), ("upload", "failed")],
        ),
    ],
)
@pytest.mark.asyncio
async def test_connection_probe_reports_early_stage_failures(
    monkeypatch,
    fail_at: str,
    expected_message: str,
    expected_steps: list[tuple[str, str]],
) -> None:
    service = S3BackupService()
    client = RecordingS3Client(fail_at=fail_at)
    install_client(monkeypatch, service, client)

    success, message, steps = await service.test_connection(make_user())

    assert success is False
    assert message == expected_message
    assert [(step["name"], step["status"]) for step in steps] == expected_steps
    await service.close()


@pytest.mark.parametrize("mismatched_download", [False, True])
@pytest.mark.asyncio
async def test_connection_probe_reports_cleanup_and_verification_failures(
    monkeypatch,
    mismatched_download: bool,
) -> None:
    service = S3BackupService()
    client = RecordingS3Client(
        fail_at="delete",
        mismatched_download=mismatched_download,
    )
    install_client(monkeypatch, service, client)

    success, message, steps = await service.test_connection(make_user())

    assert success is False
    assert "S3 delete test failed: delete denied" in message
    if mismatched_download:
        assert "Downloaded probe object did not match uploaded content" in message
        assert message.count("\n") == 1
        assert ("download", "failed") in [(step["name"], step["status"]) for step in steps]
    else:
        assert "download test failed" not in message
        assert "\n" not in message
    assert steps[-1]["status"] == "failed"
    await service.close()


@pytest.mark.asyncio
async def test_connection_probe_reports_client_construction_failure(monkeypatch) -> None:
    service = S3BackupService()
    monkeypatch.setattr(
        service,
        "_get_client",
        lambda _user: (_ for _ in ()).throw(RuntimeError("client unavailable")),
    )

    success, message, steps = await service.test_connection(make_user())

    assert success is False
    assert message == "S3 connection test failed: client unavailable"
    assert steps == [{"name": "connection", "status": "failed", "message": "client unavailable"}]


class RecordingSSHManager:
    def __init__(self, *, upload_available: bool = True, disconnect_fails: bool = False) -> None:
        self.upload_available = upload_available
        self.disconnect_fails = disconnect_fails
        self.local_path: Path | None = None
        self.disconnect_calls = 0

    async def download_file(
        self,
        _backup_path: str,
        local_path: str,
        _server: Server,
    ) -> tuple[bool, str]:
        self.local_path = Path(local_path)
        if not self.upload_available:
            return False, "SSH download failed"
        self.local_path.write_bytes(b"archive")
        return True, ""

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_fails:
            raise RuntimeError("already disconnected")


@pytest.mark.asyncio
async def test_upload_remote_backup_uses_lease_and_reports_retention_warning(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    ssh_manager = RecordingSSHManager(disconnect_fails=True)
    progress: list[str] = []
    install_client(monkeypatch, service, client)

    async def enforce_retention(user, server, *, client, progress_callback):
        assert user.id == 1
        assert server.id == 7
        assert client is not None
        await progress_callback("retention attempted")
        return False, "retention denied", 0

    monkeypatch.setattr(service, "enforce_retention", enforce_retention)

    success, message, key = await service.upload_remote_backup(
        ssh_manager,
        make_server(),
        make_user(),
        "/remote/server-backup.tar.gz",
        progress.append,
    )

    assert success is True
    assert key == "cs2/user-1/server-7/server-backup.tar.gz"
    assert "S3 upload completed" in message
    assert "retention denied" in message
    assert "retention cleanup needs attention" in message
    assert progress == [
        "Downloading backup archive to panel for S3 upload...",
        f"Uploading backup archive to S3: {key}",
        "retention attempted",
    ]
    assert ssh_manager.disconnect_calls == 1
    assert ssh_manager.local_path is not None
    assert not ssh_manager.local_path.exists()
    assert [operation for operation, _kwargs in client.calls] == ["upload_file"]
    await service.close()


@pytest.mark.asyncio
async def test_upload_remote_backup_reports_s3_failure_and_cleans_temp_file(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client(fail_at="upload_file")
    ssh_manager = RecordingSSHManager()
    install_client(monkeypatch, service, client)

    success, message, key = await service.upload_remote_backup(
        ssh_manager,
        make_server(),
        make_user(),
        "/remote/server-backup.tar.gz",
    )

    assert (success, key) == (False, None)
    assert message == "S3 upload failed: upload_file denied"
    assert ssh_manager.disconnect_calls == 1
    assert ssh_manager.local_path is not None
    assert not ssh_manager.local_path.exists()
    await service.close()


@pytest.mark.asyncio
async def test_list_backups_uses_cached_client_and_maps_listing_failure(monkeypatch) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    user = make_user()
    server = make_server()
    install_client(monkeypatch, service, client)
    expected = [{"key": "new.tar.gz"}]
    seen_clients: list[Any] = []

    def list_objects(leased_client, _user, _server):
        seen_clients.append(leased_client)
        return expected

    monkeypatch.setattr(service, "_list_backup_objects", list_objects)
    assert await service.list_backups(user, server) == (True, expected, "")
    assert seen_clients == [client]

    def failed_list(_client, _user, _server):
        raise RuntimeError("listing denied")

    monkeypatch.setattr(service, "_list_backup_objects", failed_list)
    assert await service.list_backups(user, server) == (
        False,
        [],
        "Failed to list S3 backups: listing denied",
    )
    await service.close()


@pytest.mark.asyncio
async def test_retention_without_explicit_client_leases_and_deletes_old_objects(
    monkeypatch,
) -> None:
    service = S3BackupService()
    client = RecordingS3Client()
    user = make_user(retention_count=1)
    server = make_server()
    progress: list[str] = []
    install_client(monkeypatch, service, client)
    monkeypatch.setattr(
        service,
        "_list_backup_objects",
        lambda _client, _user, _server: [
            {"key": "new.tar.gz"},
            {"key": "old-1.tar.gz"},
            {"key": "old-2.tar.gz"},
        ],
    )

    success, message, deleted = await service.enforce_retention(
        user,
        server,
        progress_callback=progress.append,
    )

    assert success is True
    assert deleted == 2
    assert "kept the newest 1 backup(s) and deleted 2" in message
    assert progress == [message]
    assert client.calls == [
        (
            "delete_objects",
            {
                "Bucket": "backups",
                "Delete": {"Objects": [{"Key": "old-1.tar.gz"}, {"Key": "old-2.tar.gz"}]},
            },
        )
    ]
    await service.close()


@pytest.mark.asyncio
async def test_retention_maps_lease_and_delete_errors(monkeypatch) -> None:
    user = make_user(retention_count=1)
    server = make_server()
    service = S3BackupService()
    monkeypatch.setattr(
        service,
        "_get_client",
        lambda _user: (_ for _ in ()).throw(RuntimeError("no client")),
    )
    assert await service.enforce_retention(user, server) == (
        False,
        "S3 retention cleanup failed: no client",
        0,
    )

    client = RecordingS3Client(delete_errors=[{"Key": "old.tar.gz", "Message": "access denied"}])
    monkeypatch.setattr(
        service,
        "_list_backup_objects",
        lambda _client, _user, _server: [
            {"key": "new.tar.gz"},
            {"key": "old.tar.gz"},
        ],
    )
    success, message, deleted = await service.enforce_retention(
        user,
        server,
        client=client,
    )
    assert (success, deleted) == (False, 0)
    assert "first error: old.tar.gz: access denied" in message


@pytest.mark.asyncio
async def test_download_backup_uses_lease_and_reports_client_error(monkeypatch, tmp_path) -> None:
    service = S3BackupService()
    user = make_user()
    server = make_server()
    object_key = service.build_backup_key(user, server, "backup.tar.gz")
    destination = tmp_path / "nested" / "backup.tar.gz"
    client = RecordingS3Client()
    install_client(monkeypatch, service, client)

    assert await service.download_backup(user, server, object_key, str(destination)) == (True, "")
    assert destination.read_bytes() == b"restored archive"

    client.fail_at = "download_file"
    failed_destination = tmp_path / "failed" / "backup.tar.gz"
    assert await service.download_backup(
        user,
        server,
        object_key,
        str(failed_destination),
    ) == (False, "Failed to download S3 backup: download_file denied")
    await service.close()
