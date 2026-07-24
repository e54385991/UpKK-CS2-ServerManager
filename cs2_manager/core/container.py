"""Per-application dependency container."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from .config import SettingsProtocol
from .resources import (
    DatabaseResourceProtocol,
    HTTPResourceProtocol,
    RedisResourceProtocol,
    SSHConnectionPoolProtocol,
    TaskSupervisorProtocol,
)

ResourceOverrides: TypeAlias = Mapping[str, object]


@dataclass(slots=True)
class AppContainer:
    """Resources and services belonging to exactly one FastAPI application.

    Legacy transports can be supplied as adapters during the migration.  The
    mapping itself is copied and made read-only so one factory call cannot
    mutate another application's resource selection.
    """

    settings: SettingsProtocol
    database: DatabaseResourceProtocol
    redis: RedisResourceProtocol
    http: HTTPResourceProtocol
    task_supervisor: TaskSupervisorProtocol
    ssh_pool: SSHConnectionPoolProtocol | None = None
    services: Mapping[str, Any] = field(default_factory=dict)
    overrides: Mapping[str, object] = field(default_factory=dict, repr=False)
    legacy_runtime: bool = True

    def __post_init__(self) -> None:
        self.services = MappingProxyType(dict(self.services))
        self.overrides = MappingProxyType(dict(self.overrides))

    def resource(self, name: str) -> object:
        """Resolve a named core resource without exposing mutable registry state."""
        try:
            return {
                "database": self.database,
                "redis": self.redis,
                "http": self.http,
                "task_supervisor": self.task_supervisor,
                "ssh_pool": self.ssh_pool,
            }[name]
        except KeyError:
            try:
                return self.services[name]
            except KeyError as exc:
                raise KeyError(f"Unknown application resource: {name}") from exc

    @property
    def resources(self) -> Mapping[str, object]:
        """Read-only view used by diagnostics and extension wiring."""
        return MappingProxyType(
            {
                **self.services,
                "database": self.database,
                "redis": self.redis,
                "http": self.http,
                "task_supervisor": self.task_supervisor,
                "ssh_pool": self.ssh_pool,
            }
        )

    @property
    def resource_overrides(self) -> Mapping[str, object]:
        """Compatibility alias matching the application factory argument."""
        return self.overrides
