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

    with pytest.raises(ValueError, match="temperature or top_p"):
        AISystemSettingsUpdate(temperature=0.2, top_p=0.9)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(history_retention_days=8)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(max_provider_rounds=1001)
    with pytest.raises(ValueError):
        AISystemSettingsUpdate(max_tool_calls_per_round=1001)


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
