"""Detached queries and orchestration for server action status."""

from __future__ import annotations

import json
import logging
import shlex
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cs2_manager.core import Principal
from modules.models import AuthType, Server

logger = logging.getLogger(__name__)


class ServerNotFoundError(LookupError):
    """The requested server is absent or is not visible to the principal."""


@dataclass(frozen=True, slots=True)
class MetamodServerTarget:
    """Only the detached fields needed for one Metamod SSH check."""

    id: int
    host: str
    ssh_port: int
    ssh_user: str
    auth_type: AuthType
    credential_revision: int
    ssh_password: str | None = field(repr=False)
    ssh_key_path: str | None
    ssh_host_key_algorithm: str | None
    ssh_host_key_fingerprint: str | None
    is_ssh_down: bool
    game_directory: str

    @property
    def is_password_auth(self) -> bool:
        return self.auth_type == AuthType.PASSWORD

    @property
    def is_key_auth(self) -> bool:
        return self.auth_type == AuthType.KEY_FILE


class ServerActionRepository:
    """Read-only server queries; transaction completion belongs to the UoW."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_metamod_target(
        self,
        server_id: int,
        principal: Principal,
    ) -> MetamodServerTarget:
        # Loading the entity is intentional during the two-release credential
        # migration: its mapper hydrates encrypted shadow columns. Only the
        # immutable snapshot crosses the transaction boundary.
        statement = select(Server).where(Server.id == server_id)
        if not principal.is_admin:
            statement = statement.where(Server.user_id == principal.id)
        result = await self._session.execute(statement)
        server = result.scalar_one_or_none()
        if server is None:
            raise ServerNotFoundError("Server not found")
        if server.id is None:
            raise RuntimeError("Persisted server is missing its id")

        return MetamodServerTarget(
            id=int(server.id),
            host=server.host,
            ssh_port=server.ssh_port,
            ssh_user=server.ssh_user,
            auth_type=server.auth_type,
            credential_revision=server.credential_revision,
            ssh_password=server.ssh_password,
            ssh_key_path=server.ssh_key_path,
            ssh_host_key_algorithm=server.ssh_host_key_algorithm,
            ssh_host_key_fingerprint=server.ssh_host_key_fingerprint,
            is_ssh_down=server.is_ssh_down,
            game_directory=server.game_directory,
        )


class CacheAdapter(Protocol):
    async def get(self, key: str) -> object: ...

    async def set(self, key: str, value: object, expire: int = 300) -> object: ...


@dataclass(frozen=True, slots=True)
class MetamodStatusResult:
    """Transport-independent result of a cached or live Metamod check."""

    success: bool
    installed: bool
    path: str | None = None
    message: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> MetamodStatusResult | None:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return None
        if not isinstance(payload, Mapping):
            return None
        success = payload.get("success")
        installed = payload.get("installed")
        if not isinstance(success, bool) or not isinstance(installed, bool):
            return None
        return cls(
            success=success,
            installed=installed,
            path=_optional_text(payload.get("path")),
            message=_optional_text(payload.get("message")),
            error=_optional_text(payload.get("error")),
        )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


class MetamodStatusService:
    """Check Metamod without depending on FastAPI or an active DB session."""

    CACHE_TTL_SECONDS = 3600

    def __init__(
        self,
        cache: CacheAdapter,
        ssh_manager_factory: Callable[[], Any],
    ) -> None:
        self._cache = cache
        self._ssh_manager_factory = ssh_manager_factory

    async def get_status(self, target: MetamodServerTarget) -> MetamodStatusResult:
        cache_key = f"metamod_status:server:{target.id}"
        try:
            cached = MetamodStatusResult.from_payload(await self._cache.get(cache_key))
            if cached is not None:
                return cached
        except Exception as exc:
            logger.warning("Failed to get Metamod status from cache: %s", exc)

        ssh_manager = self._ssh_manager_factory()
        try:
            success, message = await ssh_manager.connect(target)
            if not success:
                return MetamodStatusResult(
                    success=False,
                    installed=False,
                    error=f"Failed to connect via SSH: {message}",
                )

            metamod_path = (
                f"{target.game_directory}/cs2/game/csgo/addons/metamod/"
                "bin/linuxsteamrt64/metamod.2.cs2.so"
            )
            command = f"test -f {shlex.quote(metamod_path)} && echo 'exists'"
            _, output, _ = await ssh_manager.execute_command(command)
            installed = "exists" in output
            result = MetamodStatusResult(
                success=True,
                installed=installed,
                path=metamod_path if installed else None,
                message=(
                    "Metamod:Source is installed"
                    if installed
                    else "Metamod:Source is not installed"
                ),
            )
            try:
                await self._cache.set(
                    cache_key,
                    result.as_dict(),
                    expire=self.CACHE_TTL_SECONDS,
                )
            except Exception as exc:
                logger.warning("Failed to cache Metamod status: %s", exc)
            return result
        except Exception as exc:
            return MetamodStatusResult(
                success=False,
                installed=False,
                error=f"Error checking metamod status: {exc}",
            )
        finally:
            await ssh_manager.disconnect()
