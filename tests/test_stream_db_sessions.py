"""SSE must not pin a request-scoped DB session for the life of the stream."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.dependencies import close_request_session, get_bearer_or_cookie_user
from api.routes.v1.cleanup import scan_server_cleanup_events, scan_system_cleanup_events
from api.routes.v1.operation_inbox import stream_operation_inbox
from api.routes.v1.operations import stream_server_operation_events


def test_stream_user_does_not_depend_on_request_db():
    parameters = inspect.signature(get_bearer_or_cookie_user).parameters
    assert "db" not in parameters


def test_operation_event_stream_does_not_take_request_db():
    parameters = inspect.signature(stream_server_operation_events).parameters
    assert "db" not in parameters


def test_inbox_event_stream_does_not_take_request_db():
    parameters = inspect.signature(stream_operation_inbox).parameters
    assert "db" not in parameters


def test_cleanup_scan_stream_does_not_take_request_db():
    assert "db" not in inspect.signature(scan_server_cleanup_events).parameters
    assert "db" not in inspect.signature(scan_system_cleanup_events).parameters


@pytest.mark.asyncio
async def test_close_request_session_commits_and_closes():
    db = SimpleNamespace(is_active=True, commit=AsyncMock(), close=AsyncMock())
    await close_request_session(db)
    db.commit.assert_awaited_once()
    db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_request_session_ignores_missing_session():
    await close_request_session(None)
