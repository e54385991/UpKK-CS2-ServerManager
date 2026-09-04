"""覆盖 Redis 与 S3 适配器的成功、降级和清理路径。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.config import settings
from services.redis_manager import RedisManager
from services.s3_backup_service import S3BackupService


class _Pipeline:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return method

    async def execute(self):
        return []


class _Redis:
    def __init__(self):
        self.pipeline_obj = _Pipeline()
        self.store = {}
        self.scans = [(0, [])]
        self.calls = []

    async def set(self, key, value, **kwargs):
        self.calls.append(("set", key, value, kwargs))
        self.store[key] = value
        return True

    async def setex(self, key, expire, value):
        self.calls.append(("setex", key, expire, value))
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        self.calls.append(("delete", keys))
        return sum(self.store.pop(key, None) is not None for key in keys)

    async def exists(self, key):
        return int(key in self.store)

    async def eval(self, script, *_args):
        if "incr" in script:
            return [1, 0]
        return 1

    async def ping(self):
        return True

    async def aclose(self):
        self.calls.append(("close",))

    def pipeline(self, **_kwargs):
        return self.pipeline_obj

    async def rpush(self, *args):
        self.calls.append(("rpush", args))

    async def lpush(self, *args):
        self.calls.append(("lpush", args))

    async def ltrim(self, *args):
        self.calls.append(("ltrim", args))

    async def expire(self, *args):
        self.calls.append(("expire", args))

    async def lrange(self, key, _start, _end):
        value = self.store.get(key, [])
        return value if isinstance(value, list) else []

    async def lrem(self, *args):
        self.calls.append(("lrem", args))

    async def scan(self, *_args, **_kwargs):
        return self.scans.pop(0)

    async def mget(self, keys):
        return [self.store.get(key) for key in keys]


def _manager(monkeypatch):
    manager = RedisManager.__new__(RedisManager)
    manager.client = _Redis()
    manager._coordination_retry_after = 0
    monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", "test")
    return manager


@pytest.mark.asyncio
async def test_redis_basic_cache_lock_rate_limit_and_server_helpers(monkeypatch):
    manager = _manager(monkeypatch)
    assert manager.prefixed_key("a") == "test:a"
    assert manager.prefixed_key("test:a") == "test:a"
    assert await manager.set("value", {"x": 1}) is True
    assert await manager.get("value") == {"x": 1}
    manager.client.store["test:plain"] = "text"
    assert await manager.get("plain") == "text"
    assert await manager.get("missing") is None
    assert await manager.delete("value") is True
    assert await manager.set_server_status(3, "running") is True
    manager.client.store["test:server:3:status"] = "running"
    assert await manager.get_server_status(3) == "running"
    assert await manager.clear_server_cache(3) is True
    assert await manager.ping() is True
    await manager.close()

    assert await manager.acquire_lock("lock", "token", 30) is True
    assert await manager.is_lock_held("lock") is True
    assert await manager.get_lock_token("lock") == "token"
    assert await manager.refresh_lock("lock", "token", 30) is True
    assert await manager.release_lock("lock", "token") is True
    allowed, retry = await manager.hit_rate_limit("rate", 10, 60)
    assert allowed is True and retry == 1
    allowed, retry = await manager.hit_rate_limit("rate", 0, 60)
    assert allowed is False and retry == 1

    manager._coordination_retry_after = 10**20
    assert await manager.acquire_lock("x", "t", 1) is None
    assert await manager.is_lock_held("x") is None
    assert await manager.get_lock_token("x") is None
    assert await manager.refresh_lock("x", "t", 1) is False
    assert await manager.release_lock("x", "t") is False
    assert await manager.hit_rate_limit("x", 1, 1) == (True, 0)


@pytest.mark.asyncio
async def test_redis_failures_and_initialized_servers(monkeypatch):
    manager = _manager(monkeypatch)
    manager.client.set = AsyncMock(side_effect=RuntimeError("down"))
    manager.client.setex = AsyncMock(side_effect=RuntimeError("down"))
    assert await manager.set("x", "y") is False
    manager.client.get = AsyncMock(side_effect=RuntimeError("down"))
    assert await manager.get("x") is None
    manager.client.delete = AsyncMock(side_effect=RuntimeError("down"))
    assert await manager.delete("x") is False
    manager.client.ping = AsyncMock(side_effect=RuntimeError("down"))
    assert await manager.ping() is False

    manager.set = AsyncMock(return_value=True)
    manager.client.rpush = AsyncMock()
    manager.client.expire = AsyncMock()
    key = await manager.set_initialized_server(7, {"host": "a"}, expire=5)
    assert key.startswith("initialized_server:7:")
    manager.client.lrange = AsyncMock(return_value=[b"k1", "k2"])
    manager.get = AsyncMock(side_effect=[{"name": "one"}, None])
    servers = await manager.get_initialized_servers(7)
    assert servers == [{"name": "one", "key": "k1"}]
    manager.client.lrem = AsyncMock(side_effect=RuntimeError("list"))
    manager.delete = AsyncMock(return_value=True)
    assert await manager.delete_initialized_server(7, "k1") is True


@pytest.mark.asyncio
async def test_redis_progress_batches_and_monitoring_logs(monkeypatch):
    manager = _manager(monkeypatch)
    assert await manager.append_deployment_progress(1, "status", "hello", "now") is True
    manager.client.store["test:deployment_progress:1"] = ['{"type":"status"}']
    assert await manager.get_deployment_progress(1) == [{"type": "status"}]
    assert await manager.clear_deployment_progress(1) is True

    assert await manager.set_batch_action_status("b", 1, "pending", "wait") is True
    manager.client.scans = [(1, ["test:batch_action:b:1"]), (0, [b"test:batch_action:b:2"])]
    manager.client.store["test:batch_action:b:1"] = '{"status":"ok"}'
    manager.client.store[b"test:batch_action:b:2"] = "bad"
    assert await manager.set_batch_action_statuses("b", [1, 2], "pending") is True
    result = await manager.get_batch_action_status("b")
    assert result["1"] == {"status": "ok"}
    assert result["2"] == "bad"
    assert await manager.set_batch_action_meta("b", actor_user_id=7, action="restart") is True
    manager.client.store["test:batch_meta:b"] = '{"actor_user_id": 7}'
    assert await manager.get_batch_action_meta("b") == {"actor_user_id": 7}
    manager.client.store["test:batch_meta:bad"] = "not-json"
    assert await manager.get_batch_action_meta("bad") is None

    assert await manager.append_monitoring_log(1, "status_check", "ok", "ready") is True
    manager.client.store["test:monitoring_logs:1:status_check"] = ['{"created_at":"2025-01-01"}']
    assert await manager.get_monitoring_logs(1, "status_check") == [{"created_at": "2025-01-01"}]
    assert await manager.get_monitoring_logs(1) == [{"created_at": "2025-01-01"}]
    assert await manager.clear_monitoring_logs(1, "status_check") is True
    assert await manager.clear_monitoring_logs(1) is True


def _user(**overrides):
    values = dict(
        id=7,
        s3_enabled=True,
        s3_bucket="bucket",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
        s3_prefix="panel",
        s3_region="",
        s3_endpoint_url="https://r2.cloudflarestorage.com",
        s3_use_ssl=True,
        s3_retention_count=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _server():
    return SimpleNamespace(id=3)


class _S3Client:
    def __init__(self):
        self.deleted = []
        self.uploaded = []

    def get_paginator(self, _name):
        return SimpleNamespace(
            paginate=lambda **_kwargs: [
                {
                    "Contents": [
                        {
                            "Key": "panel/user-7/server-3/old.tar.gz",
                            "Size": 3,
                            "ETag": '"e1"',
                            "LastModified": datetime(2024, 1, 1),
                        },
                        {
                            "Key": "panel/user-7/server-3/new.tar.gz",
                            "Size": 5,
                            "ETag": '"e2"',
                            "LastModified": datetime(2025, 1, 1),
                        },
                        {"Key": "panel/user-7/server-3/", "Size": 0},
                    ]
                }
            ]
        )

    def list_objects_v2(self, **_kwargs):
        return {}

    def put_object(self, **_kwargs):
        self.payload = _kwargs.get("Body")
        return {}

    def get_object(self, **_kwargs):
        return {"Body": SimpleNamespace(read=lambda: self.payload)}

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)

    def upload_file(self, *args):
        self.uploaded.append(args)

    def download_file(self, *args):
        self.downloaded = args

    def delete_objects(self, **kwargs):
        self.deleted.append(kwargs)
        return {"Errors": []}


@pytest.mark.asyncio
async def test_s3_configuration_keys_listing_and_connection(monkeypatch, tmp_path):
    service = S3BackupService()
    user = _user()
    server = _server()
    assert service.is_configured(user)
    assert service.get_server_prefix(user, server) == "panel/user-7/server-3"
    assert (
        service.build_backup_key(user, server, "../x\\y.tar.gz")
        == "panel/user-7/server-3/xy.tar.gz"
    )
    assert service.validate_object_key(user, server, "panel/user-7/server-3/a.tar.gz")
    assert not service.validate_object_key(user, server, "panel/user-7/server-4/a.tar.gz")
    assert service.safe_object_filename("a b?.tar.gz") == "a_b_.tar.gz"
    assert service.safe_object_filename("panel/") == "backup.tar.gz"
    assert service.get_retention_count(user) == 2
    assert service.get_retention_count(_user(s3_retention_count="bad")) == 10
    assert service.get_retention_count(_user(s3_retention_count=999999)) == 10000
    assert service._get_region_name(user) == "auto"
    assert service._get_region_name(_user(s3_endpoint_url="", s3_region="eu")) == "eu"
    assert service.build_test_key(user).startswith("panel/.upkk-s3-test/user-7/")

    client = _S3Client()
    service._get_client = lambda _user: client
    ok, items, error = await service.list_backups(user, server)
    assert ok and not error and [item["filename"] for item in items] == ["new.tar.gz", "old.tar.gz"]
    ok, message, steps = await service.test_connection(user)
    assert ok and "succeeded" in message and len(steps) == 4
    assert service.is_configured(_user(s3_enabled=False)) is False
    assert await service.list_backups(_user(s3_enabled=False), server) == (
        True,
        [],
        "S3-compatible storage is not configured.",
    )
    assert await service.download_backup(
        _user(s3_enabled=False), server, "x", str(tmp_path / "x")
    ) == (False, "S3-compatible storage is not configured.")


@pytest.mark.asyncio
async def test_s3_upload_retention_download_and_failures(monkeypatch, tmp_path):
    service = S3BackupService()
    user = _user()
    server = _server()
    client = _S3Client()
    service._get_client = lambda _user: client
    ssh = SimpleNamespace(
        download_file=AsyncMock(return_value=(True, "")),
        disconnect=AsyncMock(),
    )
    ok, message, key = await service.upload_remote_backup(ssh, server, user, "/backups/a.tar.gz")
    assert ok and key.endswith("a.tar.gz") and "completed" in message
    ssh.download_file.return_value = (False, "missing")
    ok, message, key = await service.upload_remote_backup(ssh, server, user, "/backups/a.tar.gz")
    assert (ok, key) == (False, None) and "missing" in message
    ok, message, count = await service.enforce_retention(user, server, client=client)
    assert ok and count == 0
    user.s3_retention_count = 1
    ok, message, count = await service.enforce_retention(user, server, client=client)
    assert ok and count == 1
    ok, error = await service.download_backup(
        user, server, "panel/user-7/server-3/new.tar.gz", str(tmp_path / "x" / "new.tar.gz")
    )
    assert ok and not error
    ok, error = await service.download_backup(
        user, server, "panel/user-7/server-4/new.tar.gz", str(tmp_path / "bad")
    )
    assert ok is False and "outside" in error
