"""Failure-path coverage for application resources, settings, and Redis batching."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request
from pydantic import ValidationError

from api.application import (
    _database_dependency,
    create_app,
    operation_coordination_handler,
)
from modules import database as database_module
from modules.config import Settings, settings
from services.maintenance_lock import OperationCoordinationUnavailable
from services.redis_manager import RedisManager


class _Pipeline:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.operations: list[tuple] = []

    def rpush(self, *args):
        self.operations.append(("rpush", *args))
        return self

    def ltrim(self, *args):
        self.operations.append(("ltrim", *args))
        return self

    def expire(self, *args):
        self.operations.append(("expire", *args))
        return self

    def hset(self, *args, **kwargs):
        self.operations.append(("hset", *args, kwargs))
        return self

    async def execute(self):
        if self.error:
            raise self.error
        return [True] * len(self.operations)


def _manager(client) -> RedisManager:
    manager = object.__new__(RedisManager)
    manager.client = client
    manager._coordination_retry_after = 0.0
    return manager


@pytest.mark.asyncio
async def test_redis_mget_handles_empty_mixed_and_unavailable_inputs():
    client = SimpleNamespace(mget=AsyncMock(return_value=[None, b'{"ok":true}', "plain", "1"]))
    manager = _manager(client)

    assert await manager.mget([]) == []
    assert await manager.mget(["a", "b", "c", "d"]) == [None, {"ok": True}, "plain", 1]

    client.mget.side_effect = ConnectionError("redis unavailable")
    assert await manager.mget(["a", "b"]) == [None, None]


@pytest.mark.asyncio
async def test_deployment_progress_batch_handles_empty_wrapper_and_pipeline_failure():
    pipeline = _Pipeline(error=ConnectionError("write failed"))
    client = SimpleNamespace(pipeline=Mock(return_value=pipeline))
    manager = _manager(client)

    assert await manager.append_deployment_progress_batch(1, []) is True
    assert await manager.append_deployment_progress(1, "output", "line", "now") is False
    assert pipeline.operations[0][0] == "rpush"
    assert pipeline.operations[-1] == ("expire", "deployment_progress:1", 7200)


@pytest.mark.asyncio
async def test_batch_initialization_rejects_empty_and_reports_pipeline_failure():
    pipeline = _Pipeline(error=ConnectionError("write failed"))
    manager = _manager(SimpleNamespace(pipeline=Mock(return_value=pipeline)))

    assert await manager.initialize_batch_action("batch", 7, [], "pending") is False
    assert await manager.initialize_batch_action("batch", 7, [1], "pending") is False


@pytest.mark.asyncio
async def test_batch_status_without_owner_uses_one_pipeline():
    pipeline = _Pipeline()
    client = SimpleNamespace(pipeline=Mock(return_value=pipeline))
    manager = _manager(client)

    assert await manager.set_batch_action_status("batch", 4, "success") is True
    assert pipeline.operations[0][0] == "hset"
    assert pipeline.operations[1] == ("expire", "batch_action:batch", 3600)


@pytest.mark.asyncio
async def test_batch_status_filters_owner_and_invalid_json_and_fails_closed():
    client = SimpleNamespace(
        hgetall=AsyncMock(
            return_value={
                "__owner_user_id": "7",
                "1": '{"status":"success"}',
                "2": "not-json",
            }
        )
    )
    manager = _manager(client)

    assert await manager.get_batch_action_status("batch", user_id=8) == {}
    client.hgetall.return_value = {
        "__owner_user_id": "7",
        "1": '{"status":"success"}',
        "2": "not-json",
    }
    assert await manager.get_batch_action_status("batch", user_id=7) == {"1": {"status": "success"}}

    client.hgetall.return_value = {}
    assert await manager.get_batch_action_status("batch", user_id=7) == {}
    client.hgetall.side_effect = ConnectionError("read failed")
    assert await manager.get_batch_action_status("batch") == {}


@pytest.mark.asyncio
async def test_legacy_batch_scan_failure_returns_empty():
    manager = _manager(SimpleNamespace(scan=AsyncMock(side_effect=ConnectionError("scan failed"))))

    assert await manager.get_legacy_batch_action_status("legacy") == {}


def _production_settings(**updates) -> Settings:
    values = settings.model_dump()
    values.update(
        {
            "ENVIRONMENT": "production",
            "DEBUG": False,
            "SECRET_KEY": "s" * 40,
            "JWT_SECRET_KEY": "j" * 40,
            "CREDENTIAL_ENCRYPTION_KEYS": ('{"v1":"a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s"}'),
            "CREDENTIAL_ACTIVE_KEY_ID": "v1",
            "TOKEN_HASH_KEY": "t" * 40,
            "METRICS_BEARER_TOKEN": None,
        }
    )
    values.update(updates)
    return Settings(**values)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"DEBUG": True}, "DEBUG must be disabled"),
        ({"SECRET_KEY": "change-this-secret"}, "SECRET_KEY must be replaced"),
        ({"JWT_SECRET_KEY": "your_jwt_secret"}, "JWT_SECRET_KEY must be replaced"),
        ({"CREDENTIAL_ENCRYPTION_KEYS": ""}, "Credential encryption keys are required"),
        (
            {"CREDENTIAL_ENCRYPTION_KEYS": '{"v1":"not-a-32-byte-key"}'},
            "Invalid credential encryption keyring",
        ),
        ({"TOKEN_HASH_KEY": "short"}, "TOKEN_HASH_KEY must contain at least 32"),
        ({"METRICS_BEARER_TOKEN": "short"}, "METRICS_BEARER_TOKEN must contain"),
    ],
)
def test_production_settings_reject_each_insecure_configuration(updates, message):
    with pytest.raises(ValidationError, match=message):
        _production_settings(**updates)


def test_production_settings_accept_secure_configuration_and_disable_registration():
    production = _production_settings()

    assert production.registration_enabled is False
    assert _production_settings(ALLOW_REGISTRATION=True).registration_enabled is True


def test_database_and_redis_urls_escape_reserved_credential_characters():
    configured = settings.model_copy(
        update={
            "MYSQL_USER": "manager@example",
            "MYSQL_PASSWORD": "p@ss:/#word",
            "REDIS_PASSWORD": "redis@:/#secret",
        }
    )

    assert "manager%40example:p%40ss%3A%2F%23word@" in configured.mysql_url
    assert ":redis%40%3A%2F%23secret@" in configured.redis_url


@pytest.mark.asyncio
async def test_database_compatibility_commands_delegate_to_migration_layer(monkeypatch):
    from cs2_manager.infrastructure import migrations

    require_current = AsyncMock()
    coordinator = SimpleNamespace(upgrade=AsyncMock())
    coordinator_factory = Mock(return_value=coordinator)
    monkeypatch.setattr(migrations, "require_database_current", require_current)
    monkeypatch.setattr(migrations, "MigrationCoordinator", coordinator_factory)

    await database_module.init_db()
    await database_module.migrate_db()

    require_current.assert_awaited_once_with(database_module.engine)
    coordinator_factory.assert_called_once_with(database_module.engine)
    coordinator.upgrade.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_coordination_error_has_stable_503_shape():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = await operation_coordination_handler(
        request,
        OperationCoordinationUnavailable("coordination unavailable"),
    )

    assert response.status_code == 503
    assert response.body == b'{"detail":"coordination unavailable"}'


def test_application_factory_rejects_invalid_resource_override_shapes():
    with pytest.raises(TypeError, match="services.*mapping"):
        create_app(lifespan=None, resource_overrides={"services": object()})
    with pytest.raises(TypeError, match="metrics.*MetricsRegistry"):
        create_app(lifespan=None, resource_overrides={"metrics": object()})


@pytest.mark.asyncio
async def test_container_database_dependency_rolls_back_on_handler_failure():
    session = SimpleNamespace(rollback=AsyncMock())
    database = SimpleNamespace(session_factory=lambda: _SessionContext(session))
    dependency = _database_dependency(SimpleNamespace(database=database))
    generator = dependency()

    assert await anext(generator) is session
    with pytest.raises(RuntimeError, match="handler failed"):
        await generator.athrow(RuntimeError("handler failed"))
    session.rollback.assert_awaited_once()


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False
