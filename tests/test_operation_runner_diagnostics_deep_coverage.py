"""补齐插件诊断 operation worker 的账号、异常和进度事件分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.v1.operation_runner import diagnostics
from services.maintenance_lock import OperationBusyError
from services.server_operation_hub import ServerOperationConflict


class _Hub:
    def __init__(self, record):
        self.record = record
        self.finished = []
        self.running = []
        self.emitted = []

    async def get(self, _operation_id):
        return self.record

    async def mark_running(self, operation_id):
        self.running.append(operation_id)

    async def finish(self, operation_id, **kwargs):
        self.finished.append((operation_id, kwargs))

    async def emit(self, operation_id, *args, **kwargs):
        self.emitted.append((operation_id, args, kwargs))

    async def create(self, **kwargs):
        return {"operation_id": "created", **kwargs}


class _Session:
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args):
        return self.user


def _record():
    return {"operation_id": "op-diag", "server_id": 5, "actor_user_id": 7}


def _install(monkeypatch, *, user=None, record=None):
    hub = _Hub(record)
    monkeypatch.setattr(diagnostics, "server_operation_hub", hub)
    monkeypatch.setattr(diagnostics, "async_session_maker", lambda: _Session(user))
    monkeypatch.setattr(diagnostics, "_audit_terminal", AsyncMock())
    return hub


@pytest.mark.asyncio
async def test_diagnostic_progress_and_enqueue_dispatch(monkeypatch):
    hub = _install(monkeypatch, record=_record())
    progress = diagnostics._diagnostic_progress("op-diag")
    await progress("batch", {"message": "scanning"})
    await progress("heartbeat", None)
    assert hub.emitted[0][2]["message"] == "scanning"
    assert hub.emitted[1][2]["message"] == "heartbeat"

    monkeypatch.setattr(
        diagnostics, "_dispatch", AsyncMock(side_effect=lambda record, _factory: record)
    )
    execute = await diagnostics.enqueue_plugin_diagnostic_execute(
        server_id=5, actor_user_id=7, scope="both", expected_plan_hash="hash"
    )
    restore = await diagnostics.enqueue_plugin_diagnostic_restore(
        server_id=5, actor_user_id=7, diagnostic_id="d1"
    )
    resume = await diagnostics.enqueue_plugin_diagnostic_resume(
        server_id=5,
        actor_user_id=7,
        diagnostic_id="d1",
        scope="metamod",
        expected_plan_hash="hash",
    )
    assert execute["action"] == "plugin_diagnostic_execute"
    assert restore["action"] == "plugin_diagnostic_restore"
    assert resume["action"] == "plugin_diagnostic_resume"


@pytest.mark.asyncio
async def test_diagnostic_workers_handle_missing_inactive_and_terminal_payloads(monkeypatch):
    assert (
        await diagnostics.run_plugin_diagnostic_execute(
            operation_id="missing", scope="both", expected_plan_hash="hash"
        )
        is None
    )
    hub = _install(monkeypatch, user=SimpleNamespace(is_active=False), record=_record())
    await diagnostics.run_plugin_diagnostic_execute(
        operation_id="op-diag", scope="both", expected_plan_hash="hash"
    )
    assert hub.finished[-1][1]["message"] == "The operator account is no longer available"

    hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
    service = __import__("services.plugin_diagnostic_service", fromlist=["service"])
    monkeypatch.setattr(
        service,
        "execute_diagnostic_plan",
        AsyncMock(return_value={"status": "interrupted", "error": "stopped", "id": "d1"}),
    )
    await diagnostics.run_plugin_diagnostic_execute(
        operation_id="op-diag", scope="both", expected_plan_hash="hash"
    )
    assert hub.finished[-1][1] == {"success": False, "message": "stopped"}

    hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
    monkeypatch.setattr(
        service,
        "restore_diagnostic_run",
        AsyncMock(return_value={"status": "completed"}),
    )
    await diagnostics.run_plugin_diagnostic_restore(operation_id="op-diag", diagnostic_id="d1")
    assert hub.finished[-1][1]["success"] is True


@pytest.mark.asyncio
async def test_diagnostic_workers_cover_all_expected_exception_families(monkeypatch):
    service = __import__("services.plugin_diagnostic_service", fromlist=["service"])
    errors = [
        ServerOperationConflict("busy"),
        OperationBusyError("locked"),
        ValueError("bad value"),
        LookupError("missing"),
        RuntimeError("runtime"),
        HTTPException(status_code=409, detail={"reason": "conflict"}),
        Exception("unexpected"),
    ]
    for error in errors:
        hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
        monkeypatch.setattr(service, "execute_diagnostic_plan", AsyncMock(side_effect=error))
        await diagnostics.run_plugin_diagnostic_execute(
            operation_id="op-diag", scope="both", expected_plan_hash="hash"
        )
        assert hub.finished and hub.finished[-1][1]["success"] is False

        hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
        monkeypatch.setattr(service, "restore_diagnostic_run", AsyncMock(side_effect=error))
        await diagnostics.run_plugin_diagnostic_restore(operation_id="op-diag", diagnostic_id="d1")
        assert hub.finished and hub.finished[-1][1]["success"] is False

        hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
        monkeypatch.setattr(service, "restore_diagnostic_run", AsyncMock(side_effect=error))
        await diagnostics.run_plugin_diagnostic_resume(
            operation_id="op-diag",
            diagnostic_id="d1",
            scope="both",
            expected_plan_hash="hash",
        )
        assert hub.finished and hub.finished[-1][1]["success"] is False


@pytest.mark.asyncio
async def test_diagnostic_resume_success_and_inactive(monkeypatch):
    service = __import__("services.plugin_diagnostic_service", fromlist=["service"])
    hub = _install(monkeypatch, user=SimpleNamespace(is_active=True), record=_record())
    monkeypatch.setattr(
        service, "restore_diagnostic_run", AsyncMock(return_value={"status": "completed"})
    )
    monkeypatch.setattr(
        service,
        "execute_diagnostic_plan",
        AsyncMock(return_value={"status": "completed", "id": "d2"}),
    )
    await diagnostics.run_plugin_diagnostic_resume(
        operation_id="op-diag", diagnostic_id="d1", scope="both", expected_plan_hash="hash"
    )
    assert hub.finished[-1][1]["success"] is True

    hub = _install(monkeypatch, user=None, record=_record())
    await diagnostics.run_plugin_diagnostic_restore(operation_id="op-diag", diagnostic_id="d1")
    assert hub.finished[-1][1]["success"] is False
