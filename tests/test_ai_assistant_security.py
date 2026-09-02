"""Security and compatibility coverage for the AI assistant foundation."""

from __future__ import annotations

import ipaddress
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet

from modules.models import AISystemSettings, MarketPlugin, UserAISettings
from modules.schemas.ai import AISystemSettingsUpdate
from services import ai_provider, ai_security
from services.ai_provider import (
    MAX_PROVIDER_REQUEST_BYTES,
    AIPayloadTooLargeError,
    AIProviderError,
    create_chat_completion,
)
from services.ai_provider import (
    test_provider as probe_provider,
)
from services.ai_security import (
    AIConfigurationError,
    AIProviderConfig,
    decrypt_credential,
    encrypt_credential,
    get_effective_provider,
    normalize_base_url,
    redact_sensitive_text,
    sanitize_tool_result,
    validate_provider_endpoint,
)
from services.ai_tools import _safe_relative_path, canonical_arguments
from services.plugin_conflict_service import (
    PluginPlanError,
    _resolve_dependency_order,
    parse_dependency_ids,
    validate_plugin_plan_acknowledgements,
)
from services.workshop_map_service import WorkshopPlanError, fetch_workshop_details


def _sse_response(*chunks: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream; charset=utf-8"},
        content=body.encode(),
    )


def test_credentials_are_encrypted_and_never_round_trip_as_plaintext(monkeypatch):
    monkeypatch.setattr(
        ai_security.settings,
        "AI_CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    encrypted = encrypt_credential("sk-private-value")

    assert encrypted != "sk-private-value"
    assert "sk-private-value" not in encrypted
    assert decrypt_credential(encrypted) == "sk-private-value"


def test_context_window_defaults_to_256k_and_accepts_only_supported_presets():
    from pydantic import ValidationError

    assert AISystemSettings().context_window_tokens == 262_144
    assert AISystemSettingsUpdate(context_window_tokens=393_216).context_window_tokens == 393_216
    assert (
        AISystemSettingsUpdate(context_window_tokens=1_048_576).context_window_tokens == 1_048_576
    )
    with pytest.raises(ValidationError):
        AISystemSettingsUpdate(context_window_tokens=512_000)


def test_missing_ai_key_is_generated_once_in_persistent_data_file(monkeypatch, tmp_path):
    key_file = tmp_path / "data" / "ai_credential_encryption.key"
    monkeypatch.setattr(ai_security.settings, "AI_CREDENTIAL_ENCRYPTION_KEY", None)
    monkeypatch.setattr(ai_security, "CREDENTIAL_KEY_FILE", key_file)

    encrypted = encrypt_credential("sk-generated-key")
    generated = key_file.read_text(encoding="ascii").strip()

    assert generated
    assert decrypt_credential(encrypted) == "sk-generated-key"
    assert key_file.read_text(encoding="ascii").strip() == generated
    if os.name != "nt":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_explicit_ai_key_does_not_create_persistent_file(monkeypatch, tmp_path):
    key_file = tmp_path / "data" / "ai_credential_encryption.key"
    monkeypatch.setattr(
        ai_security.settings,
        "AI_CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(ai_security, "CREDENTIAL_KEY_FILE", key_file)

    encrypt_credential("sk-explicit-key")

    assert not key_file.exists()


def test_concurrent_ai_key_generation_publishes_one_complete_key(tmp_path):
    key_file = tmp_path / "data" / "ai_credential_encryption.key"

    with ThreadPoolExecutor(max_workers=8) as executor:
        keys = list(
            executor.map(lambda _index: ai_security._load_or_create_key_file(key_file), range(16))
        )

    assert len(set(keys)) == 1
    assert key_file.read_text(encoding="ascii").strip() == keys[0]
    Fernet(keys[0].encode("ascii"))


def test_ai_key_file_rejects_symbolic_links(monkeypatch, tmp_path):
    target = tmp_path / "target.key"
    target.write_text(Fernet.generate_key().decode(), encoding="ascii")
    key_file = tmp_path / "data" / "ai_credential_encryption.key"
    key_file.parent.mkdir()
    try:
        key_file.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable on this platform")
    monkeypatch.setattr(ai_security.settings, "AI_CREDENTIAL_ENCRYPTION_KEY", None)
    monkeypatch.setattr(ai_security, "CREDENTIAL_KEY_FILE", key_file)

    with pytest.raises(AIConfigurationError, match="symbolic link"):
        encrypt_credential("must-not-use-link")


@pytest.mark.asyncio
async def test_ssrf_rejects_private_resolution_unless_exact_origin_is_allowlisted(monkeypatch):
    monkeypatch.setattr(
        ai_security,
        "_host_addresses",
        lambda _host, _port: {ipaddress.ip_address("127.0.0.1")},
    )

    with pytest.raises(AIConfigurationError, match="Private"):
        await validate_provider_endpoint("https://ai.example.test/v1", [])

    assert (
        await validate_provider_endpoint("http://127.0.0.1:9000/v1", ["http://127.0.0.1:9000"])
        == "http://127.0.0.1:9000/v1"
    )


def test_provider_url_rejects_credentials_queries_and_public_http():
    with pytest.raises(AIConfigurationError):
        normalize_base_url("https://user:pass@example.com/v1")
    with pytest.raises(AIConfigurationError):
        normalize_base_url("https://example.com/v1?token=secret")


def test_secret_redaction_handles_json_tokens_passwords_and_private_keys():
    source = (
        '{"api_key":"sk-example", "password": "hunter2"}\n'
        "Authorization: Bearer abcdefghijklmnop\n"
        "-----BEGIN PRIVATE KEY-----\nprivate-data\n-----END PRIVATE KEY-----"
    )
    redacted = redact_sensitive_text(source)
    structured = sanitize_tool_result({"nested": {"ssh_password": "secret"}, "safe": "visible"})

    assert "sk-example" not in redacted
    assert "hunter2" not in redacted
    assert "private-data" not in redacted
    assert structured["nested"]["ssh_password"] == "[REDACTED]"
    assert structured["safe"] == "visible"


def test_tool_result_console_secret_redaction_preserves_json_structure():
    structured = sanitize_tool_result(
        {
            "success": True,
            "results": [{"console_output": 'sv_password "do-not-show"'}],
        }
    )

    assert structured == {
        "success": True,
        "results": [{"console_output": "sv_password [REDACTED]"}],
    }


def test_ai_paths_and_argument_hashes_are_canonical():
    assert _safe_relative_path("cs2/game/csgo/server.cfg") == "cs2/game/csgo/server.cfg"
    with pytest.raises(ValueError):
        _safe_relative_path("../outside")
    with pytest.raises(ValueError):
        _safe_relative_path("/etc/passwd")
    first = canonical_arguments({"b": 2, "a": 1})
    second = canonical_arguments({"a": 1, "b": 2})
    assert first == second


@pytest.mark.asyncio
async def test_standard_chat_completions_tool_call_probe(monkeypatch):
    original_client = httpx.AsyncClient
    client_creations = 0

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        if payload.get("tools"):
            nonce = payload["messages"][0]["content"].rsplit(" ", 1)[-1].rstrip(".")
            arguments = json.dumps({"nonce": nonce})
            return _sse_response(
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "probe",
                                        "type": "function",
                                        "function": {
                                            "name": "ai_capability_probe",
                                            "arguments": arguments[:8],
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": arguments[8:]}}
                                ]
                            }
                        }
                    ]
                },
            )
        return _sse_response(
            {"choices": [{"delta": {"role": "assistant", "content": "O"}}]},
            {"choices": [{"delta": {"content": "K"}}]},
        )

    def client_factory(**kwargs):
        nonlocal client_creations
        client_creations += 1
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test-model",
        api_key="secret",
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )

    assert await probe_provider(config) == (
        True,
        True,
        True,
        "Provider SSE text and streamed tool-calling tests passed",
    )
    assert client_creations == 1


@pytest.mark.asyncio
async def test_streaming_completion_emits_markdown_deltas(monkeypatch):
    original_client = httpx.AsyncClient
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _sse_response(
            {"choices": [{"delta": {"role": "assistant", "content": "# Status"}}]},
            {"choices": [{"delta": {"content": "\n\nRunning"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    deltas = []

    async def receive_delta(delta: str) -> None:
        deltas.append(delta)

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        [{"role": "user", "content": "status"}],
        stream=True,
        on_text_delta=receive_delta,
    )

    assert captured["stream"] is True
    assert deltas == ["# Status", "\n\nRunning"]
    assert message["content"] == "# Status\n\nRunning"
    assert message["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "total_tokens": 12,
    }


@pytest.mark.asyncio
async def test_provider_refuses_redirects(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "https://internal/"})
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
    )
    with pytest.raises(AIProviderError, match="redirect"):
        await create_chat_completion(config, [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_provider_sends_validated_optional_model_parameters(monkeypatch):
    captured = {}
    original_client = httpx.AsyncClient

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="reasoning-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
        reasoning_effort="xhigh",
        temperature=0,
        max_completion_tokens=4096,
        token_limit_parameter="max_completion_tokens",
        frequency_penalty=0.3,
        presence_penalty=-0.2,
        verbosity="high",
        parallel_tool_calls=False,
    )

    await create_chat_completion(
        config,
        [{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
    )

    assert captured["reasoning_effort"] == "xhigh"
    assert captured["temperature"] == 0
    assert captured["max_completion_tokens"] == 4096
    assert captured["frequency_penalty"] == 0.3
    assert captured["presence_penalty"] == -0.2
    assert captured["verbosity"] == "high"
    assert captured["parallel_tool_calls"] is False
    assert "top_p" not in captured
    assert "max_tokens" not in captured


@pytest.mark.asyncio
async def test_provider_compacts_oversized_history_before_sending(monkeypatch):
    original_client = httpx.AsyncClient
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    messages = [{"role": "system", "content": "Follow the system rules."}]
    messages.extend(
        {"role": "user", "content": f"old-{index} " + "x" * 10_000} for index in range(12)
    )
    messages.append({"role": "user", "content": "latest request"})

    await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        messages,
        tools=[{"type": "function", "function": {"name": "probe", "parameters": {}}}],
    )

    assert len(json.dumps(captured, ensure_ascii=False, separators=(",", ":")).encode()) <= (
        MAX_PROVIDER_REQUEST_BYTES
    )
    assert captured["messages"][-1]["content"] == "latest request"
    assert all("old-0" not in str(message) for message in captured["messages"])


@pytest.mark.asyncio
async def test_provider_root_origin_falls_back_to_conventional_v1_api(monkeypatch):
    original_client = httpx.AsyncClient
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/chat/completions":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"console")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example"),
    )

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example",
            model="test-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
        ),
        [{"role": "user", "content": "hello"}],
    )

    assert message["content"] == "OK"
    assert requested_paths == ["/chat/completions", "/v1/chat/completions"]


@pytest.mark.asyncio
async def test_provider_413_is_classified_as_non_retryable_payload_error(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(413, json={"error": "too large"})
            ),
            **kwargs,
        )

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )

    with pytest.raises(AIPayloadTooLargeError, match="HTTP 413"):
        await create_chat_completion(
            AIProviderConfig(
                base_url="https://provider.example/v1",
                model="test-model",
                api_key=None,
                timeout_seconds=10,
                allowlist=(),
                source="global",
            ),
            [{"role": "user", "content": "hello"}],
        )


@pytest.mark.asyncio
async def test_responses_protocol_maps_history_tools_and_output(monkeypatch):
    captured: dict = {}
    requested_url = ""
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_url
        requested_url = str(request.url)
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_next",
                        "name": "probe",
                        "arguments": '{"value":2}',
                    },
                ],
                "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    config = AIProviderConfig(
        base_url="https://provider.example/v1",
        model="responses-model",
        api_key=None,
        timeout_seconds=10,
        allowlist=(),
        source="global",
        api_protocol="responses",
        reasoning_effort="high",
        verbosity="low",
        max_completion_tokens=4096,
        token_limit_parameter="max_tokens",
        parallel_tool_calls=False,
    )
    message = await create_chat_completion(
        config,
        [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_old",
                        "type": "function",
                        "function": {"name": "probe", "arguments": '{"value":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_old", "content": '{"ok":true}'},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "probe",
                    "description": "Probe a value",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "probe"}},
    )

    assert requested_url == "https://provider.example/v1/responses"
    assert "messages" not in captured
    assert captured["store"] is False
    assert captured["max_output_tokens"] == 4096
    assert captured["reasoning"] == {"effort": "high"}
    assert captured["text"] == {"verbosity": "low"}
    assert captured["parallel_tool_calls"] is False
    assert captured["input"][1] == {
        "type": "function_call",
        "call_id": "call_old",
        "name": "probe",
        "arguments": '{"value":1}',
    }
    assert captured["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_old",
        "output": '{"ok":true}',
    }
    assert captured["tools"][0]["name"] == "probe"
    assert captured["tools"][0]["strict"] is False
    assert "function" not in captured["tools"][0]
    assert captured["tool_choice"] == {"type": "function", "name": "probe"}
    assert message["content"] == "Done"
    assert message["tool_calls"] == [
        {
            "id": "call_next",
            "type": "function",
            "function": {"name": "probe", "arguments": '{"value":2}'},
        }
    ]
    assert message["usage"]["input_tokens"] == 12


@pytest.mark.asyncio
async def test_responses_protocol_streams_text_and_normalizes_final_tools(monkeypatch):
    original_client = httpx.AsyncClient

    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse_response(
            {"type": "response.created", "response": {"id": "resp_stream"}},
            {"type": "response.output_text.delta", "delta": "# Status"},
            {"type": "response.output_text.delta", "delta": "\nRunning"},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_stream",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "# Status\nRunning"}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "probe",
                            "arguments": "{}",
                        },
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai_provider.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        ai_provider,
        "validate_provider_endpoint",
        AsyncMock(return_value="https://provider.example/v1"),
    )
    deltas: list[str] = []

    async def receive_delta(delta: str) -> None:
        deltas.append(delta)

    message = await create_chat_completion(
        AIProviderConfig(
            base_url="https://provider.example/v1",
            model="responses-model",
            api_key=None,
            timeout_seconds=10,
            allowlist=(),
            source="global",
            api_protocol="responses",
        ),
        [{"role": "user", "content": "status"}],
        stream=True,
        on_text_delta=receive_delta,
    )

    assert deltas == ["# Status", "\nRunning"]
    assert message["content"] == "# Status\nRunning"
    assert message["tool_calls"][0]["id"] == "call_1"
    assert message["usage"] == {"input_tokens": 3, "output_tokens": 2}


def test_model_parameter_schema_supports_extensions_and_rejects_ambiguous_sampling():
    request = AISystemSettingsUpdate(
        reasoning_effort="ultra",
        max_completion_tokens=32768,
        max_provider_rounds=1000,
        max_tool_calls_per_round=1000,
    )
    assert request.reasoning_effort == "ultra"
    assert request.max_provider_rounds == 1000
    assert request.max_tool_calls_per_round == 1000
    assert AISystemSettingsUpdate().api_protocol is None
    assert AISystemSettings().api_protocol == "chat_completions"
    assert UserAISettings(user_id=1).api_protocol == "chat_completions"

    with pytest.raises(ValueError, match="temperature or top_p"):
        AISystemSettingsUpdate(temperature=0.2, top_p=0.9)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(history_retention_days=8)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(max_provider_rounds=1001)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(max_tool_calls_per_round=1001)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(api_protocol="legacy")


def test_ai_execution_limits_default_to_200():
    settings = AISystemSettings()
    assert settings.max_provider_rounds == 200
    assert settings.max_tool_calls_per_round == 200


@pytest.mark.asyncio
async def test_personal_provider_takes_precedence(monkeypatch):
    monkeypatch.setattr(
        ai_security.settings,
        "AI_CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    system = SimpleNamespace(
        enabled=True,
        base_url="https://global.example/v1",
        model="global",
        api_key_encrypted=encrypt_credential("global-key"),
        request_timeout_seconds=30,
        private_endpoint_allowlist=[],
        admin_prompt="rules",
        provider_tested=True,
        tool_calling_tested=True,
        streaming_tested=True,
    )
    personal = UserAISettings(
        user_id=7,
        mode="custom",
        base_url="https://personal.example/v1",
        model="personal",
        api_key_encrypted=encrypt_credential("personal-key"),
        reasoning_effort="high",
        max_completion_tokens=8192,
        provider_tested=True,
        tool_calling_tested=True,
        streaming_tested=True,
    )
    monkeypatch.setattr(AISystemSettings, "get_or_create", AsyncMock(return_value=system))

    class DB:
        async def get(self, model, key):
            assert model is UserAISettings and key == 7
            return personal

    config = await get_effective_provider(DB(), SimpleNamespace(id=7))
    assert config.source == "custom"
    assert config.model == "personal"
    assert config.api_key == "personal-key"
    assert config.reasoning_effort == "high"
    assert config.max_completion_tokens == 8192
    assert config.api_protocol == "chat_completions"


@pytest.mark.asyncio
async def test_custom_provider_works_when_global_is_disabled(monkeypatch):
    monkeypatch.setattr(
        ai_security.settings,
        "AI_CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    system = SimpleNamespace(
        enabled=False,
        base_url=None,
        model=None,
        api_key_encrypted=None,
        request_timeout_seconds=30,
        private_endpoint_allowlist=[],
        admin_prompt="",
        provider_tested=False,
        tool_calling_tested=False,
        streaming_tested=False,
    )
    personal = UserAISettings(
        user_id=7,
        mode="custom",
        base_url="https://personal.example/v1",
        model="personal",
        api_key_encrypted=encrypt_credential("personal-key"),
        provider_tested=True,
        tool_calling_tested=True,
        streaming_tested=True,
    )
    monkeypatch.setattr(AISystemSettings, "get_or_create", AsyncMock(return_value=system))

    class DB:
        async def get(self, model, key):
            assert model is UserAISettings and key == 7
            return personal

    config = await get_effective_provider(DB(), SimpleNamespace(id=7))
    assert config is not None
    assert config.source == "custom"
    assert config.model == "personal"


def test_null_placeholder_test_body_is_treated_as_saved_provider():
    from api.routes.ai import _is_saved_provider_test
    from modules.schemas.ai import AIProviderTestRequest

    item = SimpleNamespace(
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
        api_protocol="chat_completions",
    )
    empty = AIProviderTestRequest()
    placeholders = AIProviderTestRequest(base_url=None, model=None, api_key=None)
    matching = AIProviderTestRequest(base_url="https://api.openai.com/v1", model="gpt-4.1")
    draft_url = AIProviderTestRequest(base_url="https://draft.example/v1")
    draft_key = AIProviderTestRequest(api_key="sk-draft")

    assert _is_saved_provider_test(empty, item) is True
    assert _is_saved_provider_test(placeholders, item) is True
    assert _is_saved_provider_test(matching, item) is True
    assert _is_saved_provider_test(draft_url, item) is False
    assert _is_saved_provider_test(draft_key, item) is False


def test_saved_probe_records_flags_without_toggling_enabled(monkeypatch):
    from api.routes.ai import _apply_saved_provider_test_flags

    monkeypatch.setattr("api.routes.ai.credential_encryption_available", lambda: True)
    item = AISystemSettings(
        enabled=True,
        base_url="https://api.example/v1",
        model="m",
        api_key_encrypted="enc",
    )
    _apply_saved_provider_test_flags(item, text_ok=True, tool_ok=True, streaming_ok=True)
    assert item.enabled is True
    assert item.provider_tested is True
    assert item.tool_calling_tested is True
    assert item.streaming_tested is True

    _apply_saved_provider_test_flags(item, text_ok=True, tool_ok=False, streaming_ok=True)
    assert item.enabled is True
    assert item.tool_calling_tested is False


@pytest.mark.asyncio
async def test_untested_enabled_system_provider_is_usable(monkeypatch):
    monkeypatch.setattr(
        ai_security.settings,
        "AI_CREDENTIAL_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    system = SimpleNamespace(
        enabled=True,
        base_url="https://global.example/v1",
        model="global",
        api_key_encrypted=encrypt_credential("global-key"),
        request_timeout_seconds=30,
        private_endpoint_allowlist=[],
        admin_prompt="",
        api_protocol="chat_completions",
        reasoning_effort=None,
        temperature=None,
        top_p=None,
        max_completion_tokens=2048,
        token_limit_parameter="max_completion_tokens",
        frequency_penalty=None,
        presence_penalty=None,
        verbosity=None,
        parallel_tool_calls=None,
        provider_tested=False,
        tool_calling_tested=False,
        streaming_tested=False,
    )
    monkeypatch.setattr(AISystemSettings, "get_or_create", AsyncMock(return_value=system))

    class DB:
        async def get(self, model, key):
            return None

    config = await get_effective_provider(DB(), SimpleNamespace(id=7))
    assert config is not None
    assert config.source == "global"
    assert config.model == "global"


def test_system_ready_to_enable_does_not_require_tests(monkeypatch):
    from api.routes.ai import _system_ready_to_enable

    monkeypatch.setattr("api.routes.ai.credential_encryption_available", lambda: True)
    item = AISystemSettings(
        enabled=False,
        base_url="https://api.example/v1",
        model="m",
        api_key_encrypted="enc",
        provider_tested=False,
        tool_calling_tested=False,
        streaming_tested=False,
    )
    assert _system_ready_to_enable(item) is True
    item.api_key_encrypted = None
    assert _system_ready_to_enable(item) is False


def test_dependency_parser_and_warning_acknowledgements():
    assert parse_dependency_ids("2, 1, 2") == [2, 1]
    with pytest.raises(PluginPlanError):
        parse_dependency_ids("2,nope")
    plan = {
        "hard_conflicts": [],
        "warnings": [{"rule_id": 8}, {"rule_id": 9}],
    }
    with pytest.raises(PluginPlanError, match="8"):
        validate_plugin_plan_acknowledgements(plan, [9])
    validate_plugin_plan_acknowledgements(plan, [8, 9])


@pytest.mark.asyncio
async def test_recursive_dependencies_are_topological_and_cycles_stop(monkeypatch):
    plugins = {
        1: SimpleNamespace(id=1, title="root", dependencies="2,3"),
        2: SimpleNamespace(id=2, title="dep2", dependencies="3"),
        3: SimpleNamespace(id=3, title="dep3", dependencies=None),
    }
    monkeypatch.setattr(
        MarketPlugin,
        "get_by_id",
        AsyncMock(side_effect=lambda _db, plugin_id: plugins.get(plugin_id)),
    )
    dependencies, target = await _resolve_dependency_order(object(), 1)
    assert [item.id for item in dependencies] == [3, 2]
    assert target.id == 1

    plugins[3].dependencies = "1"
    with pytest.raises(PluginPlanError, match="cycle"):
        await _resolve_dependency_order(object(), 1)


@pytest.mark.asyncio
async def test_workshop_validation_rejects_non_cs2_and_banned_items(monkeypatch):
    async def response_for(consumer_app_id=730, banned=0):
        return (
            True,
            {
                "response": {
                    "publishedfiledetails": [
                        {
                            "result": 1,
                            "consumer_app_id": consumer_app_id,
                            "banned": banned,
                            "title": "Workshop Map",
                        }
                    ]
                }
            },
            None,
        )

    monkeypatch.setattr(
        "services.workshop_map_service.http_helper.post",
        AsyncMock(return_value=await response_for(440, 0)),
    )
    with pytest.raises(WorkshopPlanError, match="not a Counter-Strike 2"):
        await fetch_workshop_details("3298427415")

    monkeypatch.setattr(
        "services.workshop_map_service.http_helper.post",
        AsyncMock(return_value=await response_for(730, 1)),
    )
    with pytest.raises(WorkshopPlanError, match="disabled"):
        await fetch_workshop_details("3298427415")
