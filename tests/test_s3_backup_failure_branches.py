"""覆盖 S3 兼容存储服务的探测、清理和外部 I/O 失败分支。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.s3_backup_service as s3_module
from services.s3_backup_service import S3BackupService


def _user(**overrides):
    values = dict(
        id=7,
        s3_enabled=True,
        s3_bucket="bucket",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_prefix="panel",
        s3_region="",
        s3_endpoint_url="https://objects.example.test",
        s3_use_ssl=True,
        s3_retention_count=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _server():
    return SimpleNamespace(id=3)


class _ProbeClient:
    def __init__(self, *, list_error=None, put_error=None, get_body=b"", delete_error=None):
        self.list_error = list_error
        self.put_error = put_error
        self.get_body = get_body
        self.delete_error = delete_error
        self.deleted = 0

    def list_objects_v2(self, **_kwargs):
        if self.list_error:
            raise self.list_error
        return {}

    def put_object(self, **_kwargs):
        if self.put_error:
            raise self.put_error
        return {}

    def get_object(self, **_kwargs):
        return {"Body": SimpleNamespace(read=lambda: self.get_body)}

    def delete_object(self, **_kwargs):
        if self.delete_error:
            raise self.delete_error
        self.deleted += 1


class _BackupObjectsClient:
    def __init__(self, *, delete_response=None, delete_error=None, count=3):
        self.delete_response = delete_response or {"Errors": []}
        self.delete_error = delete_error
        self.count = count
        self.deleted_calls = []

    def get_paginator(self, _name):
        contents = [
            {
                "Key": f"panel/user-7/server-3/{index}.tar.gz",
                "Size": index,
                "ETag": f'"etag-{index}"',
                "LastModified": datetime(2024, 1, index),
            }
            for index in range(1, self.count + 1)
        ]
        contents.append({"Key": "panel/user-7/server-3/", "Size": 0})
        return SimpleNamespace(paginate=lambda **_kwargs: [{"Contents": contents}])

    def delete_objects(self, **kwargs):
        self.deleted_calls.append(kwargs)
        if self.delete_error:
            raise self.delete_error
        return self.delete_response


@pytest.mark.asyncio
async def test_test_connection_reports_each_probe_failure(monkeypatch):
    service = S3BackupService()
    user = _user()

    client = _ProbeClient(list_error=RuntimeError("list denied"))
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    ok, message, steps = await service.test_connection(user)
    assert not ok and "list denied" in message
    assert [step["status"] for step in steps] == ["failed"]

    client = _ProbeClient(put_error=RuntimeError("write denied"))
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    ok, message, steps = await service.test_connection(user)
    assert not ok and "write denied" in message
    assert [step["status"] for step in steps] == ["success", "failed"]

    client = _ProbeClient(get_body=b"different")
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    ok, message, steps = await service.test_connection(user)
    assert not ok and "did not match" in message
    assert [step["status"] for step in steps] == ["success", "success", "failed", "success"]

    client = _ProbeClient(delete_error=RuntimeError("delete denied"))
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    ok, message, steps = await service.test_connection(user)
    assert not ok and "delete denied" in message
    assert steps[-1]["name"] == "delete"

    monkeypatch.setattr(service, "_get_client", lambda _user: (_ for _ in ()).throw(RuntimeError("offline")))
    ok, message, steps = await service.test_connection(user)
    assert not ok and "offline" in message and steps[0]["name"] == "connection"


def test_client_configuration_and_value_normalization(monkeypatch):
    service = S3BackupService()
    user = _user(s3_prefix=" /root/ ", s3_region=" eu-west-1 ", s3_endpoint_url="https://objects.example")
    captured = {}

    class _Config:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class _Boto:
        def client(self, name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "client"

    monkeypatch.setattr(s3_module, "boto3", _Boto())
    monkeypatch.setattr(s3_module, "Config", _Config)
    assert service._get_client(user) == "client"
    assert captured["name"] == "s3"
    assert captured["kwargs"]["region_name"] == "eu-west-1"
    assert captured["kwargs"]["endpoint_url"] == "https://objects.example"
    assert captured["config"]["s3"] == {"addressing_style": "path"}

    monkeypatch.setattr(s3_module, "boto3", None)
    with pytest.raises(RuntimeError, match="boto3"):
        service._get_client(user)

    assert service.get_retention_count(_user(s3_retention_count=None)) == 10
    assert service.get_retention_count(_user(s3_retention_count=0)) == 10
    assert service.get_retention_count(_user(s3_retention_count=-1)) == 10
    assert service.get_retention_count(_user(s3_retention_count=1.8)) == 1
    assert service.get_retention_count(_user(s3_retention_count=[])) == 10
    assert service.safe_object_filename("x" * 400) == "x" * 255
    assert service.safe_object_filename("/") == "backup.tar.gz"


@pytest.mark.asyncio
async def test_upload_retention_and_listing_errors(monkeypatch):
    service = S3BackupService()
    user = _user()
    server = _server()

    class _SSH:
        download_file = AsyncMock(return_value=(True, ""))
        disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))

    upload_client = _ProbeClient()
    upload_client.upload_file = lambda *_args: (_ for _ in ()).throw(RuntimeError("upload denied"))
    monkeypatch.setattr(service, "_get_client", lambda _user: upload_client)
    ok, message, key = await service.upload_remote_backup(
        _SSH(), server, user, "/backups/a.tar.gz"
    )
    assert not ok and key is None and "upload denied" in message

    list_client = _BackupObjectsClient(delete_error=RuntimeError("list broken"))
    monkeypatch.setattr(service, "_get_client", lambda _user: (_ for _ in ()).throw(RuntimeError("client broken")))
    assert await service.list_backups(user, server) == (
        False,
        [],
        "Failed to list S3 backups: client broken",
    )
    assert await service.enforce_retention(user, server, client=list_client) == (
        False,
        "S3 retention cleanup failed: list broken",
        0,
    )

    failing_delete = _BackupObjectsClient(
        delete_response={"Errors": [{"Key": "old.tar.gz", "Message": "denied"}]}
    )
    assert await service.enforce_retention(user, server, client=failing_delete) == (
        False,
        "S3 retention cleanup failed: failed to delete 1 backup object(s); first error: old.tar.gz: denied",
        0,
    )

    monkeypatch.setattr(service, "_get_client", lambda _user: _ProbeClient())
    ok, error = await service.download_backup(
        user, server, "panel/user-7/server-3/a.tar.gz", "backup.tar.gz"
    )
    assert not ok and "No such file" in error


@pytest.mark.asyncio
async def test_upload_retention_message_and_download_client_error(monkeypatch, tmp_path):
    service = S3BackupService()
    user = _user(s3_retention_count=1)
    server = _server()
    client = _BackupObjectsClient(
        delete_response={"Errors": [{"Key": "old.tar.gz", "Message": "denied"}]}, count=3
    )
    client.upload_file = lambda *_args: None
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    ssh = SimpleNamespace(download_file=AsyncMock(return_value=(True, "")), disconnect=AsyncMock())
    ok, message, key = await service.upload_remote_backup(
        ssh, server, user, "/backups/a.tar.gz", progress_callback=[] .append
    )
    assert ok and key.endswith("a.tar.gz") and "needs attention" in message

    broken = SimpleNamespace(download_file=lambda *_args: (_ for _ in ()).throw(RuntimeError("download denied")))
    monkeypatch.setattr(service, "_get_client", lambda _user: broken)
    ok, error = await service.download_backup(
        user, server, "panel/user-7/server-3/a.tar.gz", str(tmp_path / "x" / "a.tar.gz")
    )
    assert not ok and "download denied" in error
