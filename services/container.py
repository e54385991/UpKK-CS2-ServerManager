"""Application-scoped service and infrastructure composition root."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from modules.database import async_session_maker, engine
from modules.http_helper import HTTPHelper, http_helper
from services.redis_manager import RedisManager, redis_manager
from services.ssh_connection_pool import SSHConnectionPool, ssh_connection_pool
from services.ssh_manager import SSHManager


@dataclass(frozen=True, slots=True)
class RuntimeResources:
    """Long-lived transports shared by one application lifecycle."""

    database_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    http: HTTPHelper
    redis: RedisManager
    ssh_pool: SSHConnectionPool


@dataclass(slots=True)
class ServiceContainer:
    """Explicit application dependencies with lightweight extension points."""

    resources: RuntimeResources
    ssh_manager_factory: Callable[[], SSHManager] = SSHManager
    providers: dict[str, object] = field(default_factory=dict)

    def register(self, name: str, service: object) -> None:
        """Register a feature service during application composition."""
        if not name:
            raise ValueError("Service names cannot be empty")
        self.providers[name] = service

    def get(self, name: str) -> object:
        """Resolve a registered feature service with an actionable error."""
        try:
            return self.providers[name]
        except KeyError as exc:
            raise LookupError(f"Service is not registered: {name}") from exc

    def snapshot(self) -> Mapping[str, object]:
        """Expose an immutable diagnostic view of feature registrations."""
        return dict(self.providers)


ContainerFactory = Callable[[], ServiceContainer]


def build_service_container() -> ServiceContainer:
    """Compose the default process adapters without opening network connections."""
    return ServiceContainer(
        resources=RuntimeResources(
            database_engine=engine,
            session_factory=async_session_maker,
            http=http_helper,
            redis=redis_manager,
            ssh_pool=ssh_connection_pool,
        )
    )


__all__ = [
    "ContainerFactory",
    "RuntimeResources",
    "ServiceContainer",
    "build_service_container",
]
