"""Short read transactions for authorized telemetry batches."""

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import Server


async def load_telemetry_servers(
    db: AsyncSession, user_id: int, *, all_servers: bool = False, limit: int = 1000
) -> list[Server]:
    """Read the selected scope and release its transaction before remote I/O.

    The route authorizes the all-servers scope. Loaded scalar attributes remain
    available because the application session factory uses expire_on_commit=False.
    Probes receive these snapshots, never the session itself.
    """
    servers = (
        await Server.get_all(db, skip=0, limit=limit)
        if all_servers
        else await Server.get_all_by_user(db, user_id, skip=0, limit=limit)
    )
    await db.commit()
    return servers
