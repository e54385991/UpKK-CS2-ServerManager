"""覆盖 AI SSE/WebSocket 事件流的重放、心跳、断开和鉴权分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import ai as _ai

routes = _ai._ai_stream_routes


class _Queue:
    def __init__(self, *events):
        self.events = list(events)

    async def get(self):
        if not self.events:
            await __import__("asyncio").sleep(0)
            raise AssertionError("queue was read after terminal event")
        item = self.events.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _Hub:
    def __init__(self, replay_events=(), queue_events=()):
        self.replay_events = list(replay_events)
        self.queue = _Queue(*queue_events)
        self.unsubscribed = []
        self.subscribed = []

    async def subscribe_queue(self, _run_id):
        return self.queue

    async def replay(self, _run_id, _after):
        return list(self.replay_events)

    async def unsubscribe_queue(self, run_id, queue):
        self.unsubscribed.append((run_id, queue))

    async def subscribe(self, run_id, websocket):
        self.subscribed.append((run_id, websocket))

    async def unsubscribe(self, run_id, websocket):
        self.unsubscribed.append((run_id, websocket))


class _Request:
    def __init__(self, *states):
        self.states = list(states)

    async def is_disconnected(self):
        if self.states:
            return self.states.pop(0)
        return True


class _Ws:
    def __init__(self, receive=()):
        self.receives = list(receive)
        self.sent = []
        self.closed = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, value):
        self.sent.append(value)

    async def receive_text(self):
        value = self.receives.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self, **kwargs):
        self.closed.append(kwargs)


class _DbContext:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return self.result


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_run_lookup_and_sse_encodes_replay_queue_and_terminal(monkeypatch):
    run = SimpleNamespace(id="run-1", user_id=4, source="web")
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(run)))
    user = SimpleNamespace(id=4)
    assert await routes._run_for_user(db, user, "run-1") is run
    with pytest.raises(HTTPException) as exc_info:
        await routes._run_for_user(
            SimpleNamespace(execute=AsyncMock(return_value=_Result(None))), user, "missing"
        )
    assert exc_info.value.status_code == 404
    assert routes._encode_sse_event({"sequence": 2, "type": "line\n", "value": "中文"}).startswith(
        "id: 2\nevent: line"
    )

    hub = _Hub(
        replay_events=[
            {"sequence": 1, "type": "old"},
            {"sequence": 2, "type": "replayed"},
        ],
        queue_events=[{"sequence": 3, "type": "run_completed", "ok": True}],
    )
    monkeypatch.setattr(routes, "ai_event_hub", hub)
    monkeypatch.setattr(routes, "_run_for_user", AsyncMock(return_value=run))
    monkeypatch.setattr(routes, "close_request_session", AsyncMock())
    response = await routes.ai_run_event_stream(
        "run-1", _Request(False), after=1, db=db, current_user=user
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert chunks[0] == ": connected\n\n"
    assert any("event: replayed" in chunk for chunk in chunks)
    assert any("event: run_completed" in chunk for chunk in chunks)
    assert hub.unsubscribed == [("run-1", hub.queue)]


@pytest.mark.asyncio
async def test_sse_timeout_keepalive_and_disconnect_cleanup(monkeypatch):
    hub = _Hub(replay_events=(), queue_events=[TimeoutError()])
    monkeypatch.setattr(routes, "ai_event_hub", hub)
    monkeypatch.setattr(routes, "_run_for_user", AsyncMock())
    monkeypatch.setattr(routes, "close_request_session", AsyncMock())

    async def wait_timeout(_awaitable, _timeout=None, **_kwargs):
        _awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(routes.asyncio, "wait_for", wait_timeout)
    request = _Request(*([False] * 16))
    response = await routes.ai_run_event_stream(
        "run-2", request, after=0, db=SimpleNamespace(), current_user=SimpleNamespace(id=1)
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert ": keep-alive" in "".join(chunks)
    assert hub.unsubscribed


@pytest.mark.asyncio
async def test_websocket_auth_not_found_disconnect_and_cleanup(monkeypatch):
    unauthenticated = _Ws()
    monkeypatch.setattr(routes, "authenticate_websocket", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(routes, "ai_event_hub", _Hub())
    await routes.ai_run_events(unauthenticated, "run-1")
    assert not unauthenticated.accepted

    user = SimpleNamespace(id=4)
    missing = _Ws()
    monkeypatch.setattr(routes, "authenticate_websocket", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(routes, "async_session_maker", lambda: _DbContext(_Result(None)))
    await routes.ai_run_events(missing, "run-1")
    assert missing.closed == [{"code": 4404, "reason": "Run not found"}]

    run = SimpleNamespace(id="run-1", user_id=4, source="web")
    hub = _Hub(replay_events=[{"sequence": 1, "type": "message"}])
    monkeypatch.setattr(routes, "async_session_maker", lambda: _DbContext(_Result(run)))
    monkeypatch.setattr(routes, "ai_event_hub", hub)
    connected = _Ws(receive=[routes.WebSocketDisconnect()])
    await routes.ai_run_events(connected, "run-1", after=0)
    assert connected.accepted and connected.sent == [{"sequence": 1, "type": "message"}]
    assert hub.unsubscribed[-1] == ("run-1", connected)
