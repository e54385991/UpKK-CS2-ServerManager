"""Detached server queries and read-only SSH system-information services."""

from __future__ import annotations

import logging
import shlex
from dataclasses import asdict, dataclass, field
from typing import Literal, Mapping, Protocol, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cs2_manager.core import Principal
from modules.models import AuthType, Server

logger = logging.getLogger(__name__)


class ServerSystemInfoNotFoundError(LookupError):
    """The requested server is absent or not visible to the principal."""


@dataclass(frozen=True, slots=True)
class ServerSystemInfoTarget:
    """Minimal immutable server state safe to retain after the DB phase."""

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


class ServerSystemInfoRepository:
    """Read-only visible-server lookup; the caller owns transaction completion."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_target(
        self,
        server_id: int,
        principal: Principal,
    ) -> ServerSystemInfoTarget:
        # Selecting the mapped entity intentionally hydrates credential shadow
        # columns during the two-release encryption migration. Only this
        # immutable projection crosses the transaction boundary.
        statement = select(Server).where(Server.id == server_id)
        if not principal.is_admin:
            statement = statement.where(Server.user_id == principal.id)
        result = await self._session.execute(statement)
        server = result.scalar_one_or_none()
        if server is None:
            raise ServerSystemInfoNotFoundError("Server not found")
        if server.id is None:
            raise RuntimeError("Persisted server is missing its id")

        return ServerSystemInfoTarget(
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


class SSHManagerProtocol(Protocol):
    async def connect(self, server: ServerSystemInfoTarget) -> tuple[bool, str]: ...

    async def execute_command(
        self,
        command: str,
        timeout: int = 30,
    ) -> tuple[bool, str, str]: ...

    async def disconnect(self) -> None: ...


class CacheProtocol(Protocol):
    async def get(self, key: str) -> object: ...

    async def set(self, key: str, value: object, expire: int = 300) -> object: ...


@dataclass(frozen=True, slots=True)
class CPUCountResult:
    success: bool
    cpu_count: int
    message: str


class CPUCountService:
    """Detect a CPU count over one operation-scoped SSH manager."""

    DEFAULT_CPU_COUNT = 32

    def __init__(self, ssh_manager: object) -> None:
        self._ssh_manager = cast(SSHManagerProtocol, ssh_manager)

    async def get_cpu_count(self, target: ServerSystemInfoTarget) -> CPUCountResult:
        try:
            success, message = await self._ssh_manager.connect(target)
            if not success:
                return CPUCountResult(
                    success=False,
                    cpu_count=self.DEFAULT_CPU_COUNT,
                    message=f"Failed to connect: {message}",
                )

            for command in ("nproc", "grep -c ^processor /proc/cpuinfo"):
                command_success, stdout, _ = await self._ssh_manager.execute_command(command)
                if command_success and stdout.strip().isdigit():
                    return CPUCountResult(
                        success=True,
                        cpu_count=int(stdout.strip()),
                        message="CPU count retrieved successfully",
                    )

            return CPUCountResult(
                success=False,
                cpu_count=self.DEFAULT_CPU_COUNT,
                message="Failed to detect CPU count, using default",
            )
        except Exception as exc:
            return CPUCountResult(
                success=False,
                cpu_count=self.DEFAULT_CPU_COUNT,
                message=f"Error: {exc}",
            )
        finally:
            await self._ssh_manager.disconnect()


class DiskSpaceInfo(BaseModel):
    """Stable disk-space payload returned by the legacy endpoints."""

    used_gb: float
    total_gb: float
    available_gb: float
    used_percent: float


@dataclass(frozen=True, slots=True)
class DiskSpaceResult:
    success: bool
    server_directory: str
    disk_space: DiskSpaceInfo | None = None
    message: str | None = None


class DiskSpaceService:
    """Read and cache disk usage using only application-owned resources."""

    CACHE_TTL_SECONDS = 60 * 60

    def __init__(
        self,
        cache: CacheProtocol,
        ssh_manager: object,
    ) -> None:
        self._cache = cache
        self._ssh_manager = cast(SSHManagerProtocol, ssh_manager)

    async def get_disk_space(
        self,
        target: ServerSystemInfoTarget,
        *,
        force_refresh: bool = False,
    ) -> DiskSpaceResult:
        cache_key = f"disk_space:{target.id}"
        if not force_refresh:
            try:
                cached = self._validated_disk_info(await self._cache.get(cache_key))
                if cached is not None:
                    return DiskSpaceResult(
                        success=True,
                        server_directory=target.game_directory,
                        disk_space=cached,
                    )
            except Exception as exc:
                logger.warning("Failed to read disk-space cache for server %s: %s", target.id, exc)

        disk_info = await self._read_disk_space(target)
        if disk_info is None:
            return DiskSpaceResult(
                success=False,
                server_directory=target.game_directory,
                message="Failed to retrieve disk space information",
            )

        try:
            await self._cache.set(
                cache_key,
                disk_info.model_dump(),
                expire=self.CACHE_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("Failed to cache disk space for server %s: %s", target.id, exc)
        return DiskSpaceResult(
            success=True,
            server_directory=target.game_directory,
            disk_space=disk_info,
        )

    async def _read_disk_space(
        self,
        target: ServerSystemInfoTarget,
    ) -> DiskSpaceInfo | None:
        try:
            success, _ = await self._ssh_manager.connect(target)
            if not success:
                return None

            escaped_path = shlex.quote(target.game_directory)
            du_command = f"du -sb {escaped_path} 2>/dev/null | awk '{{print $1}}' || echo '0'"
            success, stdout, _ = await self._ssh_manager.execute_command(
                du_command,
                timeout=60,
            )
            if not success:
                return None
            try:
                used_gb = int(stdout.strip() or "0") / (1024**3)
            except TypeError, ValueError:
                return None

            success, stdout, _ = await self._ssh_manager.execute_command(
                f"df -BG {escaped_path} | tail -1"
            )
            if not success or not stdout:
                return None
            return self._parse_df_output(stdout, used_gb)
        except Exception as exc:
            logger.warning("Failed to read disk space for server %s: %s", target.id, exc)
            return None
        finally:
            await self._ssh_manager.disconnect()

    @staticmethod
    def _validated_disk_info(value: object) -> DiskSpaceInfo | None:
        if not isinstance(value, Mapping):
            return None
        try:
            return DiskSpaceInfo.model_validate(value)
        except ValidationError:
            return None

    @staticmethod
    def _parse_df_output(output: str, used_gb: float) -> DiskSpaceInfo | None:
        parts = output.split()
        if len(parts) < 5:
            return None
        try:
            total_gb = float(parts[1].rstrip("G"))
            available_gb = float(parts[3].rstrip("G"))
        except TypeError, ValueError:
            return None
        used_percent = (used_gb / total_gb) * 100 if total_gb > 0 else 0.0
        return DiskSpaceInfo(
            used_gb=round(used_gb, 2),
            total_gb=round(total_gb, 2),
            available_gb=round(available_gb, 2),
            used_percent=round(used_percent, 2),
        )


@dataclass(frozen=True, slots=True)
class DeploymentCheckResult:
    is_deployed: bool
    binary_path: str
    message: str
    error: bool


class DeploymentCheckService:
    """Verify the CS2 binary without retaining ORM state."""

    def __init__(self, ssh_manager: object) -> None:
        self._ssh_manager = cast(SSHManagerProtocol, ssh_manager)

    async def check(self, target: ServerSystemInfoTarget) -> DeploymentCheckResult:
        binary_path = f"{target.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
        command = f"test -f {shlex.quote(binary_path)} && echo 'exists' || echo 'missing'"
        try:
            success, message = await self._ssh_manager.connect(target)
            if not success:
                return DeploymentCheckResult(
                    is_deployed=False,
                    binary_path=binary_path,
                    message=f"Could not connect to server: {message}",
                    error=True,
                )

            verify_success, stdout, _ = await self._ssh_manager.execute_command(command)
            is_deployed = verify_success and "exists" in stdout
            return DeploymentCheckResult(
                is_deployed=is_deployed,
                binary_path=binary_path,
                message="Server is deployed" if is_deployed else "Server is not deployed",
                error=False,
            )
        except Exception as exc:
            return DeploymentCheckResult(
                is_deployed=False,
                binary_path=binary_path,
                message=f"Error checking deployment: {exc}",
                error=True,
            )
        finally:
            await self._ssh_manager.disconnect()


class CPUCountResponse(BaseModel):
    success: bool
    cpu_count: int
    message: str


class DiskSpaceSuccessResponse(BaseModel):
    success: Literal[True]
    disk_space: DiskSpaceInfo
    server_directory: str


class DiskSpaceFailureResponse(BaseModel):
    success: Literal[False]
    message: str
    server_directory: str


DiskSpaceResponse = DiskSpaceSuccessResponse | DiskSpaceFailureResponse


class DeploymentCheckResponse(BaseModel):
    is_deployed: bool
    binary_path: str
    message: str
    error: bool


def cpu_count_response(result: CPUCountResult) -> CPUCountResponse:
    return CPUCountResponse.model_validate(asdict(result))


def disk_space_response(result: DiskSpaceResult) -> DiskSpaceResponse:
    if result.success and result.disk_space is not None:
        return DiskSpaceSuccessResponse(
            success=True,
            disk_space=result.disk_space,
            server_directory=result.server_directory,
        )
    return DiskSpaceFailureResponse(
        success=False,
        message=result.message or "Failed to retrieve disk space information",
        server_directory=result.server_directory,
    )


def deployment_check_response(result: DeploymentCheckResult) -> DeploymentCheckResponse:
    return DeploymentCheckResponse.model_validate(asdict(result))
