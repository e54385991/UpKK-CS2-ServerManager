"""Provider quotas count actual attempts across concurrent callers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.contracts.v1.assistant import AssistantSystemSettingsPatch
from modules.models import AISystemSettings, UserAISettings
from modules.schemas.ai import AISystemSettingsUpdate
from services.ai import transport
from services.ai_security import get_effective_provider


@pytest.mark.asyncio
async def test_rpm_window_expires_at_sixty_seconds_and_cleans_old_keys(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)
    limiter = transport.AIProviderTransport()
    await limiter.acquire_rpm(2, "https://example.com/v1", "key")
    clock[0] += 10
    await limiter.acquire_rpm(2, "https://example.com/v1", "key")
    await limiter.acquire_rpm(2, "https://example.com/v1", "key")
    assert sleeps == [50]
    assert list(next(iter(limiter._rpm_windows.values()))) == [1010, 1060]
    clock[0] += 60
    await limiter.acquire_rpm(2, "https://another.example", "key")
    assert len(limiter._rpm_windows) == 1


@pytest.mark.asyncio
async def test_rpm_shares_origin_credential_across_paths_and_can_cancel(monkeypatch):
    waiting = asyncio.Event()
    blocked = asyncio.Event()

    async def sleep(_seconds):
        waiting.set()
        await blocked.wait()

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)
    limiter = transport.AIProviderTransport()
    await limiter.acquire_rpm(1, "https://EXAMPLE.com/v1", "private-key")
    task = asyncio.create_task(
        limiter.acquire_rpm(1, "https://example.com:443/other", "private-key")
    )
    await waiting.wait()
    assert not task.done()
    await limiter.acquire_rpm(1, "https://example.com/v1", "other-key")
    await limiter.acquire_rpm(1, "https://other.example/v1", "private-key")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(limiter._rpm_windows) == 3
    assert all(len(window) == 1 for window in limiter._rpm_windows.values())
    assert "private-key" not in repr(limiter._rpm_windows)


@pytest.mark.asyncio
async def test_concurrent_callers_never_exceed_rpm_and_cancelled_waiters_use_no_slot(monkeypatch):
    waiting = asyncio.Event()
    blocked = asyncio.Event()
    count = 0

    async def sleep(_seconds):
        nonlocal count
        count += 1
        if count == 5:
            waiting.set()
        await blocked.wait()

    monkeypatch.setattr(transport.asyncio, "sleep", sleep)
    limiter = transport.AIProviderTransport()
    tasks = [
        asyncio.create_task(limiter.acquire_rpm(3, "https://example.com", "key")) for _ in range(8)
    ]
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        assert sum(task.done() for task in tasks) == 3
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert len(next(iter(limiter._rpm_windows.values()))) == 3


@pytest.mark.parametrize("value", [0, -1, 10001, 1.5])
def test_rpm_api_rejects_invalid_values(value):
    for schema in (AISystemSettingsUpdate, AssistantSystemSettingsPatch):
        with pytest.raises(ValidationError):
            schema(requests_per_minute=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_system_rpm_is_resolved_for_global_and_personal_provider(monkeypatch, custom):
    system = AISystemSettings(
        enabled=True, base_url="https://example.com", model="test", requests_per_minute=17
    )
    personal = (
        UserAISettings(user_id=1, mode="custom", base_url="https://personal.example", model="test")
        if custom
        else None
    )
    monkeypatch.setattr(AISystemSettings, "get_or_create", AsyncMock(return_value=system))
    db = AsyncMock()
    db.get.return_value = personal
    provider = await get_effective_provider(db, SimpleNamespace(id=1))
    assert provider is not None
    assert provider.requests_per_minute == 17


@pytest.mark.asyncio
async def test_invalid_internal_rpm_cannot_silently_disable_limit():
    with pytest.raises(ValueError, match="RPM"):
        await transport.AIProviderTransport().acquire_rpm(0, "https://example.com", None)


@pytest.mark.asyncio
async def test_every_wire_attempt_acquires_rpm_including_adaptive_retry(monkeypatch):
    from contextlib import asynccontextmanager

    import httpx

    from services import ai_provider, http_retry
    from services.ai_security import AIProviderConfig
    from services.http_retry import BackgroundRetry

    events = []
    responses = [
        httpx.Response(413),
        httpx.Response(429),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
    ]

    async def acquire(*args):
        events.append(("acquire", args))

    @asynccontextmanager
    async def stream(*args, **kwargs):
        events.append(("request", kwargs["json"]))
        yield responses.pop(0)

    monkeypatch.setattr(ai_provider.ai_provider_transport, "acquire_rpm", acquire)
    monkeypatch.setattr(ai_provider.ai_provider_transport, "stream", stream)
    monkeypatch.setattr(
        ai_provider, "validate_provider_endpoint", AsyncMock(return_value="https://example.com/v1")
    )
    monkeypatch.setattr(http_retry.asyncio, "sleep", AsyncMock())
    config = AIProviderConfig(
        base_url="https://example.com/v1",
        model="test",
        api_key="key",
        timeout_seconds=1,
        allowlist=(),
        source="global",
        requests_per_minute=17,
    )
    result = await ai_provider.create_chat_completion(
        config,
        [{"role": "user", "content": "test"}],
        retry=BackgroundRetry(AsyncMock(), AsyncMock()),
    )
    assert result["content"] == "ok"
    assert [event[0] for event in events] == ["acquire", "request"] * 3
    assert all(args == (17, config.base_url, "key") for kind, args in events if kind == "acquire")
    assert all(
        "requests_per_minute" not in payload for kind, payload in events if kind == "request"
    )
