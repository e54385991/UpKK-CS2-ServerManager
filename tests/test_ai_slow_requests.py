"""Slow background requests stay observable and cancellable without replay."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from services import ai_provider, http_retry
from services.ai_security import AIProviderConfig
from services.http_retry import BackgroundRetry


@pytest.mark.asyncio
async def test_slow_request_reports_wait_then_returns_without_retry(monkeypatch):
    release = asyncio.Event()
    request = AsyncMock(side_effect=release.wait)
    waiting = AsyncMock(side_effect=release.set)
    check = AsyncMock()
    notify = AsyncMock()
    original_wait = asyncio.wait

    async def fast_wait(tasks, *, timeout):
        assert timeout == 15
        return await original_wait(tasks, timeout=0.001)

    monkeypatch.setattr(http_retry.asyncio, "wait", fast_wait)
    assert await BackgroundRetry(check, notify, waiting).run(request, lambda _: 0)
    request.assert_awaited_once()
    waiting.assert_awaited_once()
    assert check.await_count == 2
    notify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_slow_request_cleans_up_on_access_change_or_cancellation(monkeypatch, cancel_parent):
    started = asyncio.Event()
    cleaned = asyncio.Event()
    original_wait = asyncio.wait

    async def fast_wait(tasks, *, timeout):
        return await original_wait(tasks, timeout=0.001)

    async def request():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(http_retry.asyncio, "wait", fast_wait)
    check = AsyncMock(side_effect=[None, PermissionError()])
    retry = BackgroundRetry(check, AsyncMock(), AsyncMock())
    task = asyncio.create_task(retry.run(request, lambda _: None))
    await started.wait()
    if cancel_parent:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel_parent else PermissionError):
        await task
    assert cleaned.is_set()
    retry.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_json_response_retries_and_recovers(monkeypatch):
    config = AIProviderConfig(
        base_url="https://example.com/v1",
        model="test",
        api_key="secret",
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )
    responses = iter(
        [
            b"<html>upstream unavailable</html>",
            b'{"choices":[{"message":{"content":"recovered"}}]}',
        ]
    )

    async def request():
        return ai_provider._decode_provider_message(config, next(responses))

    monkeypatch.setattr(http_retry.asyncio, "sleep", AsyncMock())
    retry = BackgroundRetry(AsyncMock(), AsyncMock())
    result = await retry.run(request, ai_provider._retry_hint)
    assert result["content"] == "recovered"
    retry.notify.assert_awaited_once()
