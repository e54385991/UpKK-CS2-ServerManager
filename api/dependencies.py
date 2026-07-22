"""Shared FastAPI dependencies for authorization and operation serialization."""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules import Server, User, get_current_active_user, get_db
from services.maintenance_lock import maintenance_lock_service


async def locked_server_operation(
    request: Request,
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Authorize and serialize a destructive server operation across processes."""
    server = await db.get(Server, server_id)
    if server is None or (not current_user.is_admin and server.user_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    await db.commit()
    operation = f"{request.method}:{request.url.path}"
    async with maintenance_lock_service.get(
        server_id,
        operation=operation,
        wait=False,
        ttl=7200,
    ):
        yield server
