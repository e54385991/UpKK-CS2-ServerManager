"""Configuration caching and startup validation contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.config import Settings, get_settings, settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_USER": "panel",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DATABASE": "panel",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": 6379,
        "REDIS_DB": 0,
        "REDIS_POOL_SIZE": 4,
        "REDIS_HEALTH_CHECK_INTERVAL": 30,
        "REDIS_SOCKET_CONNECT_TIMEOUT": 5,
        "REDIS_SOCKET_TIMEOUT": 5,
        "API_HOST": "127.0.0.1",
        "API_PORT": 8000,
        "BACKEND_URL": "http://127.0.0.1:8000",
        "LOG_LEVEL": "info",
        "ASYNCSSH_LOG_LEVEL": "warning",
        "SECRET_KEY": "s" * 32,
        "JWT_SECRET_KEY": "j" * 32,
        "JWT_ALGORITHM": "HS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": 60,
        "SSH_AUTH_MODE": "password",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_is_cached_once_and_compatibility_export_is_the_cached_value():
    assert get_settings() is get_settings()
    assert settings is get_settings()


def test_production_is_the_default_runtime_mode():
    assert Settings.model_fields["DEBUG"].default is False
    assert Settings.model_fields["RUN_MODE"].default == "production"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("API_PORT", 0),
        ("REDIS_PORT", 65536),
        ("DB_POOL_SIZE", 0),
        ("DB_MAX_OVERFLOW", -1),
        ("REDIS_DB", -1),
        ("LOG_LEVEL", "verbose"),
        ("SSH_AUTH_MODE", "agent"),
        ("LEGACY_HTML_CONSOLE", "render"),
        ("RUN_MODE", "staging"),
        ("BACKEND_URL", "localhost:8000"),
        ("SECRET_KEY", "short"),
    ],
)
def test_settings_reject_invalid_operational_values(field: str, value: object):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_settings_normalizes_case_for_enum_like_values():
    value = _settings(
        LOG_LEVEL="info",
        ASYNCSSH_LOG_LEVEL="error",
        SSH_AUTH_MODE="BOTH",
        RUN_MODE="DEVELOPMENT",
        BACKEND_URL="http://127.0.0.1:8000/",
    )

    assert value.LOG_LEVEL == "INFO"
    assert value.ASYNCSSH_LOG_LEVEL == "ERROR"
    assert value.SSH_AUTH_MODE == "both"
    assert value.RUN_MODE == "development"
    assert value.BACKEND_URL == "http://127.0.0.1:8000"
