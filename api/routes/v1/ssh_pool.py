"""Versioned SSH connection-pool snapshot for the console chrome."""

from fastapi import APIRouter

from api.dependencies import ActiveUser
from services.ssh_connection_pool import ssh_connection_pool

from .schemas import SshPoolView

router = APIRouter(prefix="/api/v1/ssh-pool", tags=["v1-ssh-pool"])


async def read_ssh_pool_view() -> SshPoolView:
    """Project in-memory pool counters without secrets or host credentials."""
    stats = await ssh_connection_pool.get_pool_stats()
    return SshPoolView(
        connections=int(stats.get("alive_connections") or 0),
        in_use=int(stats.get("in_use_connections") or 0),
        idle=int(stats.get("idle_connections") or 0),
        leases=int(stats.get("active_leases") or 0),
        draining=int(stats.get("draining_connections") or 0),
        idle_timeout=int(stats.get("idle_timeout") or 900),
        max_lifetime=int(stats.get("max_lifetime") or 3600),
        keepalive_interval=int(stats.get("keepalive_interval") or 30),
        keepalive_count_max=int(stats.get("keepalive_count_max") or 3),
    )


@router.get("", response_model=SshPoolView)
async def read_ssh_pool(_current_user: ActiveUser) -> SshPoolView:
    """Return the current SSH pool size so the console can show live usage."""
    return await read_ssh_pool_view()
