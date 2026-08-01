"""Credential, endpoint, and output security for the AI assistant."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import anyio
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from modules.config import settings
from modules.models import AISystemSettings, User, UserAISettings

MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOOL_RESULT_CHARS = 20_000


class AIConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AIProviderConfig:
    base_url: str
    model: str
    api_key: str | None
    timeout_seconds: int
    allowlist: tuple[str, ...]
    source: str
    admin_prompt: str = ""


def _fernet() -> Fernet:
    value = (settings.AI_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if not value:
        raise AIConfigurationError("AI_CREDENTIAL_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AIConfigurationError(
            "AI_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc


def encrypt_credential(value: str) -> str:
    value = value.strip()
    if not value:
        raise AIConfigurationError("Credential cannot be blank")
    return _fernet().encrypt(value.encode()).decode("ascii")


def decrypt_credential(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode()
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise AIConfigurationError(
            "The saved AI credential cannot be decrypted; re-enter the API key"
        ) from exc


def normalize_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AIConfigurationError("Endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AIConfigurationError("Endpoint cannot contain credentials, query, or fragment")
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    origin = normalize_origin(value)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), urlsplit(origin).netloc, path, "", ""))


def normalize_allowlist(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        origin = normalize_origin(value)
        if urlsplit(value).path not in {"", "/"}:
            raise AIConfigurationError("Private endpoint allowlist entries must be origins")
        if origin not in normalized:
            normalized.append(origin)
    return normalized


def _host_addresses(host: str, port: int) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        addresses.add(ipaddress.ip_address(item[4][0]))
    return addresses


async def validate_provider_endpoint(base_url: str, allowlist: list[str] | tuple[str, ...]) -> str:
    normalized = normalize_base_url(base_url)
    parsed = urlsplit(normalized)
    origin = normalize_origin(normalized)
    normalized_allowlist = set(normalize_allowlist(list(allowlist)))
    if origin in normalized_allowlist:
        return normalized
    if parsed.scheme != "https":
        raise AIConfigurationError("Public AI endpoints must use HTTPS")
    port = parsed.port or 443
    try:
        addresses = await anyio.to_thread.run_sync(_host_addresses, parsed.hostname or "", port)
    except OSError as exc:
        raise AIConfigurationError("AI endpoint hostname could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise AIConfigurationError(
            "Private, loopback, link-local, and reserved AI endpoints require an admin allowlist entry"
        )
    return normalized


_SECRET_LINE = re.compile(
    r"(?im)^(\s*(?:rcon_password|sv_password|password|secret|token|api[_-]?key|"
    r"webhook(?:_url)?|authorization)\s*[=:]\s*)([^\r\n]+)$"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN = re.compile(r"\b(?:gh[poushr]_|github_pat_)[A-Za-z0-9_]{8,}")
_SECRET_INLINE = re.compile(
    r"""(?ix)(["']?(?:rcon_password|sv_password|password|secret|token|api[_-]?key|"""
    r"""webhook(?:_url)?|authorization|ssh[_-]?(?:key|password))["']?\s*[:=]\s*["']?)"""
    r"""([^"'\s,}]+)"""
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_SENSITIVE_KEYS = {
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "webhook",
    "webhook_url",
    "ssh_key",
    "ssh_password",
    "rcon_password",
    "sv_password",
}


def redact_sensitive_text(value: str, *, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    value = _SECRET_LINE.sub(r"\1[REDACTED]", value)
    value = _SECRET_INLINE.sub(r"\1[REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _COMMON_TOKEN.sub("[REDACTED_TOKEN]", value)
    value = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    if len(value) > limit:
        value = value[:limit] + "\n[TRUNCATED]"
    return value


def sanitize_tool_result(value: object) -> dict:
    def redact_object(item: object) -> object:
        if isinstance(item, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if str(key).casefold().replace("-", "_") in _SENSITIVE_KEYS
                    else redact_object(child)
                )
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [redact_object(child) for child in item]
        if isinstance(item, str):
            return redact_sensitive_text(item)
        return item

    value = redact_object(value)
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    serialized = redact_sensitive_text(serialized)
    try:
        parsed = json.loads(serialized)
    except json.JSONDecodeError:
        parsed = {"output": serialized}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


async def get_effective_provider(
    db: AsyncSession, user: User, *, require_tested: bool = True
) -> AIProviderConfig | None:
    system = await AISystemSettings.get_or_create(db)
    if not system.enabled:
        return None
    personal = await db.get(UserAISettings, user.id)
    if personal is not None and personal.mode == "custom":
        if require_tested and not (personal.provider_tested and personal.tool_calling_tested):
            return None
        if not personal.base_url or not personal.model:
            return None
        return AIProviderConfig(
            base_url=personal.base_url,
            model=personal.model,
            api_key=decrypt_credential(personal.api_key_encrypted),
            timeout_seconds=system.request_timeout_seconds,
            allowlist=tuple(system.private_endpoint_allowlist or []),
            source="custom",
            admin_prompt=(system.admin_prompt or "").strip(),
        )
    if require_tested and not (system.provider_tested and system.tool_calling_tested):
        return None
    if not system.base_url or not system.model:
        return None
    return AIProviderConfig(
        base_url=system.base_url,
        model=system.model,
        api_key=decrypt_credential(system.api_key_encrypted),
        timeout_seconds=system.request_timeout_seconds,
        allowlist=tuple(system.private_endpoint_allowlist or []),
        source="global",
        admin_prompt=(system.admin_prompt or "").strip(),
    )
