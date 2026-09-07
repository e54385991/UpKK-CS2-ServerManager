"""Transient background failures retry only the request, within bounded budgets."""

import asyncio
import json
from email.utils import formatdate
from unittest.mock import AsyncMock

import httpx
import pytest

from services import ai_provider, http_retry
from services.ai.errors import AIProviderError
from services.ai_security import AIProviderConfig
from services.http_retry import BackgroundRetry, RetryExhaustedError, retry_after_seconds
from services.plugins import ai_import_runner as runner
from services.plugins import ai_import_store as store
from services.plugins.github_ai_client import GitHubAIClient, GitHubAuthenticationError


@pytest.fixture
def retry(monkeypatch):
    monkeypatch.setattr(http_retry.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(http_retry.random, "uniform", lambda _low, _high: 0)
    return BackgroundRetry(AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_retry_budget_and_capped_backoff(retry):
    request = AsyncMock(side_effect=RuntimeError("private upstream credentials"))
    with pytest.raises(RetryExhaustedError, match="10 attempts") as error:
        await retry.run(request, lambda _: 0)
    assert "private" not in str(error.value)
    assert request.await_count == 10
    assert [call.args for call in retry.notify.call_args_list] == [
        (index + 2, min(60, 2 ** (index + 1))) for index in range(9)
    ]
    assert sum(call.args[0] for call in http_retry.asyncio.sleep.call_args_list) == 302


@pytest.mark.asyncio
async def test_wait_honors_retry_after_and_cancellation(retry):
    request = AsyncMock(side_effect=RuntimeError())
    http_retry.asyncio.sleep.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await retry.run(request, lambda _: 120)
    assert request.await_count == 1
    retry.notify.assert_awaited_once_with(2, 120)


@pytest.mark.asyncio
async def test_changed_access_stops_before_retry(retry):
    request = AsyncMock(side_effect=RuntimeError())
    retry.check.side_effect = [None, PermissionError()]
    with pytest.raises(PermissionError):
        await retry.run(request, lambda _: 0)
    assert request.await_count == 1


def test_retry_after_dates_and_malformed_values(monkeypatch):
    monkeypatch.setattr(http_retry.time, "time", lambda: 1000)
    assert retry_after_seconds("45") == 45
    assert retry_after_seconds(formatdate(1120, usegmt=True)) == 120
    for value in [None, "", "invalid", "-1", "nan", "inf"]:
        assert retry_after_seconds(value) == 0


@pytest.fixture
def provider(monkeypatch):
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test-model",
        api_key="private-key",
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )
    monkeypatch.setattr(
        ai_provider, "validate_provider_endpoint", AsyncMock(return_value=config.base_url)
    )
    original_client = httpx.AsyncClient

    def setup(handler):
        monkeypatch.setattr(
            ai_provider.httpx,
            "AsyncClient",
            lambda **kwargs: original_client(
                **{"transport": httpx.MockTransport(handler), **kwargs}
            ),
        )
        return config

    return setup


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 408, 429, 500, 502, 503, 504])
async def test_ai_transient_response_recovers(provider, retry, status):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                status, headers={"Retry-After": "17"}, json={"error": {"type": "rate_limit_error"}}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "success"}}]})

    result = await ai_provider.create_chat_completion(
        provider(handle), [{"role": "user", "content": "test"}], retry=retry
    )
    assert result["content"] == "success"
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    if status != 200:
        retry.notify.assert_awaited_once_with(2, 17)
    await ai_provider.ai_provider_transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 413])
async def test_ai_permanent_errors_and_adaptive_413_are_not_replayed(provider, retry, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "rejected"}})

    with pytest.raises(AIProviderError):
        await ai_provider.create_chat_completion(
            provider(handle), [{"role": "user", "content": "test"}], retry=retry
        )
    assert len(requests) == (2 if status == 413 else 1)
    retry.notify.assert_not_awaited()
    if status == 413:
        assert requests[0].content != requests[1].content
    await ai_provider.ai_provider_transport.close()


@pytest.mark.asyncio
async def test_adaptive_payload_transient_retry_does_not_resend_original(provider, retry):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(413)
        if len(requests) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await ai_provider.create_chat_completion(
        provider(handle), [{"role": "user", "content": "test"}], retry=retry
    )
    assert len(requests) == 3
    assert requests[0] != requests[1] == requests[2]
    await ai_provider.ai_provider_transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.ProxyError,
        httpx.RemoteProtocolError,
        httpx.DecodingError,
    ],
)
async def test_ai_and_github_transport_errors_attempt_ten_times(provider, retry, error_type):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        raise error_type("private token", request=request)

    with pytest.raises(RetryExhaustedError):
        await ai_provider.create_chat_completion(
            provider(handle), [{"role": "user", "content": "test"}], retry=retry
        )
    assert calls == 10
    await ai_provider.ai_provider_transport.close()
    calls = 0
    client = GitHubAIClient("token", interval=0, retry=retry, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RetryExhaustedError):
            await client.request("/user")
        assert calls == 10
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 503])
async def test_github_background_http_retry_recovers(retry, status):
    responses = [
        httpx.Response(status, headers={"Retry-After": "10"}),
        httpx.Response(200, json={"login": "test"}),
    ]
    client = GitHubAIClient(
        "token", interval=0, retry=retry, transport=httpx.MockTransport(lambda _: responses.pop(0))
    )
    try:
        assert await client.request("/user") == {"login": "test"}
        assert not responses
        assert retry.notify.await_count == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_github_credentials_never_retry(retry):
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    client = GitHubAIClient("token", interval=0, retry=retry, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(GitHubAuthenticationError):
            await client.request("/user")
        assert calls == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_import_retry_progress_and_exhaustion_are_safe(monkeypatch):
    from dataclasses import replace

    from modules.models import PluginImportJob
    from modules.plugin_ai import ImportOptions

    job = store.snapshot(
        PluginImportJob(
            actor_user_id=1,
            request_key="test",
            options=ImportOptions().model_dump(),
            command="AI import",
        )
    )
    update = AsyncMock()
    monkeypatch.setattr(store, "update_job", update)
    monkeypatch.setattr(
        store, "credentials", AsyncMock(side_effect=RetryExhaustedError("private key"))
    )
    await runner.run_job(job)
    assert update.call_args.kwargs["reason"] == "retry_exhausted"
    assert "10 attempts" in update.call_args.kwargs["message"]
    assert "private" not in update.call_args.kwargs["message"]

    config = AIProviderConfig(
        base_url="https://example.com/v1",
        model="test",
        api_key="private-key",
        timeout_seconds=1,
        allowlist=(),
        source="global",
    )
    instance = runner.ImportRunner(replace(job), "token", config)
    try:
        await instance.ai_retry_progress(2, 8)
        assert "2/10" in update.call_args.kwargs["message"]
        await instance.github_retry_progress(3, 16)
        assert "3/10" in update.call_args.kwargs["message"]
    finally:
        await instance.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "rate_limit", "truncated"])
async def test_buffered_ai_stream_restarts_without_partial_output(provider, retry, failure):
    requests = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("private connection detail")

    def handle(request):
        requests.append(json.loads(request.content))
        headers = {"content-type": "text/event-stream"}
        if len(requests) == 1:
            if failure == "disconnect":
                return httpx.Response(200, headers=headers, stream=BrokenStream())
            content = (
                'data: {"error":{"type":"rate_limit_error"}}\n\n'
                if failure == "rate_limit"
                else 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            )
            return httpx.Response(200, headers=headers, text=content)
        return httpx.Response(
            200,
            headers=headers,
            text='data: {"choices":[{"delta":{"content":"complete"}}]}\n\ndata: [DONE]\n\n',
        )

    result = await ai_provider.create_chat_completion(
        provider(handle), [{"role": "user", "content": "test"}], stream=True, retry=retry
    )
    assert result["content"] == "complete"
    assert len(requests) == 2
    assert all(request["stream"] for request in requests)
    await ai_provider.ai_provider_transport.close()


@pytest.mark.asyncio
async def test_stream_with_external_text_callback_is_never_replayed(provider, retry):
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    with pytest.raises(AIProviderError):
        await ai_provider.create_chat_completion(
            provider(handle),
            [{"role": "user", "content": "test"}],
            stream=True,
            on_text_delta=AsyncMock(),
            retry=retry,
        )
    assert calls == 1
    retry.notify.assert_not_awaited()
    await ai_provider.ai_provider_transport.close()


def test_analysis_negative_classification_and_safe_validation_errors():
    from services.plugins.ai_analysis import AnalysisFormatError, parse_analysis

    assert not parse_analysis('```json\n{"is_plugin":false}\n```').is_plugin
    with pytest.raises(AnalysisFormatError, match="not valid JSON"):
        parse_analysis("private prose")
    with pytest.raises(AnalysisFormatError, match="schema mismatch") as error:
        parse_analysis('{"title":"private-value", "private-secret-key":"secret"}')
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_job_deadline_interrupts_long_retry_wait(monkeypatch):
    from types import SimpleNamespace

    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def check():
        pass

    async def notify(_attempt, _delay):
        entered.set()

    async def fail():
        raise RuntimeError("temporary")

    async def run(_self):
        try:
            await BackgroundRetry(check, notify).run(fail, lambda _: 3600)
        finally:
            stopped.set()

    monkeypatch.setattr(store, "credentials", AsyncMock(return_value=("token", None)))
    monkeypatch.setattr(runner.ImportRunner, "__init__", lambda self, *_args: None)
    monkeypatch.setattr(runner.ImportRunner, "run", run)
    update = AsyncMock()
    monkeypatch.setattr(store, "update_job", update)
    await runner.run_job(
        SimpleNamespace(actor_user_id=1, operation_id="job", options=SimpleNamespace(minutes=0.001))
    )
    assert entered.is_set() and stopped.is_set()
    assert update.call_args.kwargs["reason"] == "timeout"
