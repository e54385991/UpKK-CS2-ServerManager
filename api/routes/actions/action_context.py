"""Dependencies shared by one server action; mutable records remain request-owned."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from modules import DeploymentLog, Server, User
from services.ssh_manager import SSHManager


@dataclass(frozen=True)
class ActionContext:
    server_id: int
    server: Server
    db: AsyncSession
    current_user: User
    ssh_manager: SSHManager
    log: DeploymentLog
    deployment_lock_key: str
    progress_callback: Callable[[str], Awaitable[None]]
    clear_crash_protection: Callable[[], Awaitable[None]]
    clear_execstack: bool
    clear_execstack_targets: object
    action: str
