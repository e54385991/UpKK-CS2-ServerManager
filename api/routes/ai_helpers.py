"""AI settings DTO helpers shared by HTTP route handlers."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules import (
    AIProviderTestRequest,
    AISystemSettings,
    AISystemSettingsResponse,
    AISystemSettingsUpdate,
    User,
    UserAISettings,
    UserAISettingsResponse,
    UserAISettingsUpdate,
)
from services.ai_security import (
    AIConfigurationError,
    credential_encryption_available,
    encrypt_credential,
    get_effective_provider,
    normalize_allowlist,
    normalize_base_url,
)

_MODEL_PARAMETER_NAMES = (
    "reasoning_effort",
    "temperature",
    "top_p",
    "max_completion_tokens",
    "token_limit_parameter",
    "frequency_penalty",
    "presence_penalty",
    "verbosity",
    "parallel_tool_calls",
)


def _model_parameters(item: AISystemSettings | UserAISettings) -> dict[str, Any]:
    return {name: getattr(item, name) for name in _MODEL_PARAMETER_NAMES}


def _api_protocol(value: str) -> Literal["chat_completions", "responses"]:
    return "responses" if value == "responses" else "chat_completions"


def _user_mode(value: str) -> Literal["global", "custom"]:
    return "custom" if value == "custom" else "global"


def _effective_source(value: str) -> Literal["global", "custom", "none"]:
    if value == "global":
        return "global"
    if value == "custom":
        return "custom"
    return "none"


def _validate_sampling_parameters(parameters: dict[str, Any]) -> None:
    if parameters["temperature"] is not None and parameters["top_p"] is not None:
        raise AIConfigurationError("Set temperature or top_p, not both")


def _apply_model_parameters(
    request: AISystemSettingsUpdate | UserAISettingsUpdate,
    item: AISystemSettings | UserAISettings,
) -> bool:
    parameters = _model_parameters(item)
    for name in _MODEL_PARAMETER_NAMES:
        if name in request.model_fields_set:
            value = getattr(request, name)
            if name == "max_completion_tokens" and value is None:
                value = 2048
            elif name == "token_limit_parameter" and value is None:
                value = "max_completion_tokens"
            parameters[name] = value
    _validate_sampling_parameters(parameters)
    changed = False
    for name, value in parameters.items():
        changed |= value != getattr(item, name)
        setattr(item, name, value)
    return changed


def _test_model_parameters(
    request: AIProviderTestRequest,
    item: AISystemSettings | UserAISettings,
) -> dict[str, Any]:
    parameters = _model_parameters(item)
    for name in _MODEL_PARAMETER_NAMES:
        if name in request.model_fields_set and getattr(request, name) is not None:
            parameters[name] = getattr(request, name)
    _validate_sampling_parameters(parameters)
    return parameters


def _system_response(item: AISystemSettings) -> AISystemSettingsResponse:
    api_protocol = _api_protocol(item.api_protocol)
    parameters = _model_parameters(item)
    parameters["token_limit_parameter"] = (
        item.token_limit_parameter
        if item.token_limit_parameter in {"max_completion_tokens", "max_tokens", "omit"}
        else "max_completion_tokens"
    )
    return AISystemSettingsResponse(
        enabled=item.enabled,
        base_url=item.base_url,
        model=item.model,
        api_protocol=api_protocol,
        api_key_configured=bool(item.api_key_encrypted),
        admin_prompt=item.admin_prompt,
        private_endpoint_allowlist=item.private_endpoint_allowlist or [],
        **parameters,
        request_timeout_seconds=item.request_timeout_seconds,
        history_retention_days=item.history_retention_days,
        max_provider_rounds=item.max_provider_rounds,
        max_tool_calls_per_round=getattr(item, "max_tool_calls_per_round", 200),
        context_window_tokens=getattr(item, "context_window_tokens", 262_144),
        requests_per_minute=getattr(item, "requests_per_minute", 60),
        provider_tested=item.provider_tested,
        tool_calling_tested=item.tool_calling_tested,
        streaming_tested=item.streaming_tested,
    )


async def _user_response(
    db: AsyncSession, user: User, item: UserAISettings
) -> UserAISettingsResponse:
    source = "none"
    enabled = False
    try:
        effective = await get_effective_provider(db, user)
    except AIConfigurationError:
        effective = None
    if effective is not None:
        source = effective.source
        enabled = True
    mode = _user_mode(item.mode)
    api_protocol = _api_protocol(item.api_protocol)
    parameters = _model_parameters(item)
    parameters["token_limit_parameter"] = (
        item.token_limit_parameter
        if item.token_limit_parameter in {"max_completion_tokens", "max_tokens", "omit"}
        else "max_completion_tokens"
    )
    effective_source = _effective_source(source)
    return UserAISettingsResponse(
        mode=mode,
        base_url=item.base_url,
        model=item.model,
        api_protocol=api_protocol,
        api_key_configured=bool(item.api_key_encrypted),
        **parameters,
        provider_tested=item.provider_tested,
        tool_calling_tested=item.tool_calling_tested,
        streaming_tested=item.streaming_tested,
        effective_enabled=enabled,
        effective_source=effective_source,
    )


async def _get_user_settings(db: AsyncSession, user_id: int) -> UserAISettings:
    item = await db.get(UserAISettings, user_id)
    if item is None:
        item = UserAISettings(user_id=user_id)
        db.add(item)
        await db.commit()
        await db.refresh(item)
    return item


def _configuration_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _is_saved_provider_test(
    request: AIProviderTestRequest,
    item: AISystemSettings | UserAISettings,
) -> bool:
    """Persist probe flags only when the caller is testing the stored provider.

    The Jinja UI posts ``{}``. The Next console used to post null placeholders
    for URL / model / key; those must still record the saved configuration.
    An explicit API key or a different URL / model / protocol is a dry-run.
    """
    if request.api_key:
        return False
    if request.base_url:
        try:
            if normalize_base_url(request.base_url) != item.base_url:
                return False
        except AIConfigurationError, ValueError:
            return False
    posted_model = (request.model or "").strip()
    if posted_model and posted_model != (item.model or ""):
        return False
    if (
        "api_protocol" in request.model_fields_set
        and request.api_protocol is not None
        and request.api_protocol != item.api_protocol
    ):
        return False
    return True


def _system_ready_to_enable(item: AISystemSettings) -> bool:
    return bool(
        item.base_url
        and item.model
        and item.api_key_encrypted
        and credential_encryption_available()
    )


def _apply_saved_provider_test_flags(
    item: AISystemSettings | UserAISettings,
    *,
    text_ok: bool,
    tool_ok: bool,
    streaming_ok: bool,
) -> None:
    item.provider_tested = text_ok
    item.tool_calling_tested = tool_ok
    item.streaming_tested = streaming_ok


def _apply_system_provider_fields(
    item: AISystemSettings,
    request: AISystemSettingsUpdate,
) -> bool:
    changed = False
    try:
        if "base_url" in request.model_fields_set:
            normalized = normalize_base_url(request.base_url) if request.base_url else None
            changed |= normalized != item.base_url
            item.base_url = normalized
        if "model" in request.model_fields_set:
            model = (request.model or "").strip() or None
            changed |= model != item.model
            item.model = model
        if "api_protocol" in request.model_fields_set and request.api_protocol is not None:
            changed |= request.api_protocol != item.api_protocol
            item.api_protocol = request.api_protocol
        if request.api_key:
            item.api_key_encrypted = encrypt_credential(request.api_key)
            changed = True
        elif request.clear_api_key:
            item.api_key_encrypted = None
            changed = True
        if request.private_endpoint_allowlist is not None:
            allowlist = normalize_allowlist(request.private_endpoint_allowlist)
            changed |= allowlist != item.private_endpoint_allowlist
            item.private_endpoint_allowlist = allowlist
        return changed | _apply_model_parameters(request, item)
    except (AIConfigurationError, ValueError) as exc:
        raise _configuration_error(exc) from exc


def _apply_system_runtime_limits(item: AISystemSettings, request: AISystemSettingsUpdate) -> None:
    for field in (
        "admin_prompt",
        "request_timeout_seconds",
        "max_provider_rounds",
        "max_tool_calls_per_round",
        "history_retention_days",
        "context_window_tokens",
        "requests_per_minute",
    ):
        value = getattr(request, field)
        if value is not None:
            setattr(item, field, value.strip() or None if field == "admin_prompt" else value)


def _apply_system_enabled(item: AISystemSettings, request: AISystemSettingsUpdate) -> None:
    if request.enabled is None:
        return
    if request.enabled and not _system_ready_to_enable(item):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Configure encryption, URL, model, and API key before enabling AI",
        )
    item.enabled = request.enabled
