"""Structural configuration contract used by the application boundary."""

from __future__ import annotations

from typing import Literal, Protocol


class SettingsProtocol(Protocol):
    """Settings needed to construct process resources.

    Keeping this structural lets tests and deployments supply immutable
    settings objects without mutating the legacy module-level singleton.
    """

    MYSQL_POOL_SIZE: int
    MYSQL_MAX_OVERFLOW: int
    MYSQL_POOL_TIMEOUT: int
    MYSQL_POOL_RECYCLE: int
    MYSQL_POOL_PRE_PING: bool
    MYSQL_ECHO: bool

    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str | None
    REDIS_DB: int
    REDIS_POOL_SIZE: int
    REDIS_HEALTH_CHECK_INTERVAL: int
    REDIS_SOCKET_CONNECT_TIMEOUT: int
    REDIS_SOCKET_TIMEOUT: int

    ENVIRONMENT: Literal["development", "test", "production"]
    METRICS_BEARER_TOKEN: str | None

    @property
    def mysql_url(self) -> str: ...

    @property
    def redis_url(self) -> str: ...


__all__ = ["SettingsProtocol"]
