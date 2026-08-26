"""Credential, endpoint, and output security for the AI assistant."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import anyio
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from modules.config import settings
from modules.models import AISystemSettings, User, UserAISettings

MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOOL_RESULT_CHARS = 20_000
CREDENTIAL_KEY_FILE = Path(__file__).resolve().parents[1] / "data" / "ai_credential_encryption.key"

logger = logging.getLogger(__name__)


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
    api_protocol: str = "chat_completions"
    admin_prompt: str = ""
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int = 2048
    token_limit_parameter: str = "max_completion_tokens"
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    verbosity: str | None = None
    parallel_tool_calls: bool | None = None


def _read_key_file(path: Path) -> str:
    if path.is_symlink():
        raise AIConfigurationError("AI credential key file cannot be a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AIConfigurationError(f"Unable to read AI credential key file: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 512:
            raise AIConfigurationError("AI credential key file must be a small regular file")
        value = os.read(descriptor, 512).decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise AIConfigurationError("AI credential key file is unreadable") from exc
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise AIConfigurationError("Unable to secure AI credential key file permissions") from exc
    return value


def _load_or_create_key_file(path: Path | None = None) -> str:
    path = path or CREDENTIAL_KEY_FILE
    if path.parent.is_symlink():
        raise AIConfigurationError("AI credential data directory cannot be a symbolic link")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    except OSError as exc:
        raise AIConfigurationError(
            f"Unable to create AI credential data directory: {path.parent}"
        ) from exc
    if path.exists() or path.is_symlink():
        return _read_key_file(path)

    generated = Fernet.generate_key()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise AIConfigurationError(f"Unable to stage AI credential key file: {path}") from exc
    write_error: OSError | None = None
    try:
        payload = generated + b"\n"
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    except OSError as exc:
        write_error = exc
    finally:
        os.close(descriptor)
    if write_error is not None:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AIConfigurationError("Unable to persist AI credential key file") from write_error
    created = False
    try:
        os.link(temporary, path, follow_symlinks=False)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise AIConfigurationError(f"Unable to publish AI credential key file: {path}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if not created:
        return _read_key_file(path)
    os.chmod(path, 0o600)
    logger.info("Generated persistent AI credential encryption key at %s", path)
    return generated.decode("ascii")


def _fernet() -> Fernet:
    value = (settings.AI_CREDENTIAL_ENCRYPTION_KEY or "").strip()
    if not value:
        value = _load_or_create_key_file()
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AIConfigurationError(
            "AI credential encryption key must be a valid Fernet key"
        ) from exc


def initialize_credential_encryption() -> str:
    """Generate or validate encryption material without exposing the key."""
    _fernet()
    return (
        "environment"
        if (settings.AI_CREDENTIAL_ENCRYPTION_KEY or "").strip()
        else str(CREDENTIAL_KEY_FILE)
    )


def credential_encryption_available() -> bool:
    try:
        _fernet()
    except AIConfigurationError:
        return False
    return True


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
_SECRET_CONSOLE_COMMAND = re.compile(
    r"(?im)\b(rcon_password|sv_password)"
    r"(\s+)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s;\r\n]+)"
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
    value = _SECRET_CONSOLE_COMMAND.sub(r"\1\2[REDACTED]", value)
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
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return redact_sensitive_text(str(item))

    value = redact_object(value)
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) > MAX_TOOL_RESULT_CHARS:
        return {"output": serialized[:MAX_TOOL_RESULT_CHARS] + "\n[TRUNCATED]"}
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
        if require_tested and not (
            personal.provider_tested and personal.tool_calling_tested and personal.streaming_tested
        ):
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
            api_protocol=getattr(personal, "api_protocol", "chat_completions"),
            admin_prompt=(system.admin_prompt or "").strip(),
            reasoning_effort=personal.reasoning_effort,
            temperature=personal.temperature,
            top_p=personal.top_p,
            max_completion_tokens=personal.max_completion_tokens,
            token_limit_parameter=personal.token_limit_parameter,
            frequency_penalty=personal.frequency_penalty,
            presence_penalty=personal.presence_penalty,
            verbosity=personal.verbosity,
            parallel_tool_calls=personal.parallel_tool_calls,
        )
    if require_tested and not (
        system.provider_tested and system.tool_calling_tested and system.streaming_tested
    ):
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
        api_protocol=getattr(system, "api_protocol", "chat_completions"),
        admin_prompt=(system.admin_prompt or "").strip(),
        reasoning_effort=system.reasoning_effort,
        temperature=system.temperature,
        top_p=system.top_p,
        max_completion_tokens=system.max_completion_tokens,
        token_limit_parameter=system.token_limit_parameter,
        frequency_penalty=system.frequency_penalty,
        presence_penalty=system.presence_penalty,
        verbosity=system.verbosity,
        parallel_tool_calls=system.parallel_tool_calls,
    )
