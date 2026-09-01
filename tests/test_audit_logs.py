"""Coverage for administrator audit logging and 30-day retention."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.application import create_app
from modules.models import AuditLog, DiscordOperationRun
from modules.schemas import AuditLogListResponse, AuditLogResponse
from services.audit_log_service import (
    AUDIT_CATEGORIES,
    AUDIT_LOG_RETENTION_DAYS,
    AUDIT_STATUSES,
    INVALID_CREDENTIALS_DETAILS,
    discord_operation_details,
    record_audit_event,
    retention_cutoff,
)
from services.audit_retention_service import AuditRetentionService


class _RecordingSession:
    def __init__(self, items=None, rowcounts=None):
        self.added = []
        self.items = list(items or [])
        self.rowcounts = list(rowcounts or [])
        self.committed = False
        self.statements = []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def execute(self, statement):
        self.statements.append(statement)
        if self.rowcounts:
            return SimpleNamespace(rowcount=self.rowcounts.pop(0), scalars=lambda: self)

        class _Scalars:
            def __init__(self, items):
                self._items = items

            def all(self):
                return self._items

        return SimpleNamespace(scalars=lambda: _Scalars(self.items))

    def all(self):
        return self.items

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _operation(**overrides) -> DiscordOperationRun:
    values = {
        "id": "op-1",
        "server_id": 7,
        "owner_user_id": 3,
        "actor_user_id": "999",
        "guild_id": "100",
        "channel_id": "200",
        "action": "start",
        "required_capabilities": ["control_power"],
        "arguments": {"mode": "now"},
        "arguments_hash": "hash",
        "plan_snapshot": {"action": "start"},
        "plan_hash": "plan",
        "status": "pending",
        "expires_at": datetime.now() - timedelta(minutes=1),
    }
    values.update(overrides)
    return DiscordOperationRun(**values)


@pytest.mark.asyncio
async def test_record_audit_event_writes_redacted_details(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr("services.audit_log_service.async_session_maker", lambda: session)

    await record_audit_event(
        category="settings",
        action="profile.update",
        status="success",
        actor_username="alice",
        ip_address="203.0.113.8",
        details={"token": "super-secret-token", "note": "ok"},
    )

    assert session.committed is True
    item = session.added[0]
    assert isinstance(item, AuditLog)
    assert item.category == "settings"
    assert item.action == "profile.update"
    assert item.actor_username == "alice"
    assert item.ip_address == "203.0.113.8"
    assert item.details["token"] == "[REDACTED]"
    assert item.details["note"] == "ok"


@pytest.mark.asyncio
async def test_record_audit_event_failure_does_not_raise(monkeypatch):
    class _BrokenSession(_RecordingSession):
        async def commit(self):
            raise RuntimeError("db down")

    monkeypatch.setattr("services.audit_log_service.async_session_maker", lambda: _BrokenSession())
    await record_audit_event(category="auth", action="login", status="success")


def test_failed_login_details_do_not_distinguish_unknown_users():
    assert INVALID_CREDENTIALS_DETAILS == {"reason": "invalid_credentials"}


def test_discord_game_console_details_omit_ciphertext():
    item = _operation(
        action="game_console",
        arguments={"command_encrypted": "cipher", "command_hash": "abc", "extra": "keep"},
        error="password=hunter2 failed",
    )
    details = discord_operation_details(item)
    assert details["command_present"] is True
    assert "command_encrypted" not in details
    assert "arguments" not in details
    assert "[REDACTED]" in details["error"]


def test_discord_change_map_details_omit_ciphertext():
    item = _operation(
        action="change_map",
        arguments={"command_encrypted": "cipher", "command_hash": "abc", "name": "ze_saw_p"},
    )
    details = discord_operation_details(item)
    assert details["command_present"] is True
    assert "command_encrypted" not in details
    assert "arguments" not in details


@pytest.mark.asyncio
async def test_retention_expires_pending_operations_and_records_audit(monkeypatch):
    item = _operation()
    session = _RecordingSession(items=[item])
    recorded = []

    async def capture(operation, status):
        recorded.append((operation.id, status))

    monkeypatch.setattr("services.audit_retention_service.async_session_maker", lambda: session)
    monkeypatch.setattr(
        "services.audit_retention_service.record_discord_operation_event",
        capture,
    )
    expired = await AuditRetentionService().expire_pending_discord_operations()
    assert expired == 1
    assert item.status == "expired"
    assert recorded == [("op-1", "expired")]


@pytest.mark.asyncio
async def test_retention_deletes_rows_older_than_30_days(monkeypatch):
    session = _RecordingSession(rowcounts=[4, 2])
    monkeypatch.setattr("services.audit_retention_service.async_session_maker", lambda: session)
    deleted_audit, deleted_ops = await AuditRetentionService().delete_expired_rows()
    assert deleted_audit == 4
    assert deleted_ops == 2
    assert session.committed is True
    assert AUDIT_LOG_RETENTION_DAYS == 30
    assert retention_cutoff() < datetime.now() + timedelta(days=1)


@pytest.mark.asyncio
async def test_retention_keeps_recent_cutoff_inside_30_days():
    cutoff = retention_cutoff(now=datetime(2026, 8, 26, 12, 0, 0))
    assert cutoff == datetime(2026, 7, 27, 12, 0, 0)


def test_audit_logs_api_requires_authentication():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/api/system/audit-logs")
    assert response.status_code == 401


def test_audit_logs_api_rejects_non_admin(monkeypatch):
    from modules import get_current_active_user, get_current_user

    app = create_app(lifespan=None)
    user = SimpleNamespace(id=2, username="member", is_admin=False, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    response = client.get("/api/system/audit-logs")
    assert response.status_code == 403


def test_audit_logs_api_lists_rows_for_admin(monkeypatch):
    from modules import get_current_active_user, get_current_admin_user, get_current_user, get_db

    payload = AuditLogListResponse(
        items=[
            AuditLogResponse(
                id="a1",
                created_at=None,
                category="auth",
                action="login",
                status="success",
                actor_user_id=1,
                actor_username="admin",
                ip_address="127.0.0.1",
                user_agent="pytest",
                source="web",
                details={},
            )
        ],
        total=1,
        limit=50,
        offset=0,
        retention_days=30,
    )
    monkeypatch.setattr(
        "api.routes.system_settings.list_audit_logs",
        AsyncMock(return_value=payload),
    )
    app = create_app(lifespan=None)
    admin = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)

    async def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_current_active_user] = lambda: admin
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    app.dependency_overrides[get_db] = fake_db
    client = TestClient(app)
    response = client.get("/api/system/audit-logs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "login"
    assert body["retention_days"] == 30


def test_audit_categories_include_files_config_plugin():
    assert {"files", "config", "plugin"} <= AUDIT_CATEGORIES
    assert "partial" in AUDIT_STATUSES


def test_v1_audit_filters_new_category_and_partial_status(monkeypatch):
    from modules import get_current_active_user, get_current_admin_user, get_current_user, get_db

    listed = AsyncMock(return_value=SimpleNamespace(items=[], total=0, limit=50, offset=0))
    monkeypatch.setattr("api.routes.v1.audit.list_audit_logs", listed)
    app = create_app(lifespan=None)
    admin = SimpleNamespace(id=1, username="admin", is_admin=True, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_current_active_user] = lambda: admin
    app.dependency_overrides[get_current_admin_user] = lambda: admin

    async def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = fake_db
    client = TestClient(app)
    assert client.get("/api/v1/audit", params={"category": "nope"}).status_code == 400
    assert client.get("/api/v1/audit", params={"status": "bogus"}).status_code == 400
    response = client.get("/api/v1/audit", params={"category": "files", "status": "partial"})
    assert response.status_code == 200
    assert listed.await_args.kwargs["category"] == "files"
    assert listed.await_args.kwargs["status"] == "partial"
