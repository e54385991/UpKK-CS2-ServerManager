"""Authorize a server set, then build an inbox payload without holding the DB."""

from __future__ import annotations

from typing import Any, Protocol

from sqlmodel import select

from modules import Server
from modules.database import async_session_maker
from services.operations.inbox_types import InboxItemData, InboxPayload
from services.plugins.ai_import_store import list_jobs
from services.server_operation_history import (
    COMPLETED_RETENTION_SECONDS,
    FAILED_RETENTION_SECONDS,
)
from services.server_operation_hub import server_operation_hub


class InboxUser(Protocol):
    id: int
    is_admin: bool


class InboxDatabase(Protocol):
    async def execute(self, statement: Any) -> Any: ...


async def load_accessible_servers(
    db: InboxDatabase, current_user: InboxUser
) -> list[tuple[int, str]]:
    """Read id/name for servers the caller may see. Caller must end the transaction."""
    if current_user.is_admin:
        result = await db.execute(select(Server.id, Server.name))
    else:
        result = await db.execute(
            select(Server.id, Server.name).where(Server.user_id == current_user.id)
        )
    return [(int(row[0]), str(row[1])) for row in result.all()]


async def list_accessible_servers(current_user: InboxUser) -> list[tuple[int, str]]:
    """Short-lived session for streams that must not pin the request connection."""
    async with async_session_maker() as db:
        return await load_accessible_servers(db, current_user)


def _item(row, server_name: str) -> InboxItemData:
    return InboxItemData(
        record=row.record,
        server_name=server_name,
        queue_position=row.queue_position,
        latest_message=row.latest_message,
    )


def _label(server_id: int, server_name: str, names: dict[int, str]) -> str:
    return server_name or names.get(server_id) or f"#{server_id}"


async def build_operation_inbox(
    servers: list[tuple[int, str]],
    include_imports: bool = False,
) -> InboxPayload:
    names = {server_id: name for server_id, name in servers}
    server_ids = [server_id for server_id, _name in servers]
    snapshot = await server_operation_hub.snapshot_for_servers(server_ids)
    items: list[InboxItemData] = []
    completed_items: list[InboxItemData] = []
    failed_items: list[InboxItemData] = []
    for server_id, server_name in servers:
        label = _label(server_id, server_name, names)
        slice_ = snapshot.slices.get(server_id)
        if slice_ is None:
            continue
        items.extend(_item(row, label) for row in slice_.active)
        failed_items.extend(_item(row, label) for row in slice_.failed)
        completed_items.extend(_item(row, label) for row in slice_.completed)
    items.sort(
        key=lambda item: (
            0 if item.record.get("status") == "running" else 1,
            str(item.record.get("started_at") or ""),
        )
    )
    completed_items.sort(
        key=lambda item: str(
            item.record.get("completed_at") or item.record.get("started_at") or ""
        ),
        reverse=True,
    )
    failed_items.sort(
        key=lambda item: str(
            item.record.get("completed_at") or item.record.get("started_at") or ""
        ),
        reverse=True,
    )
    jobs = await list_jobs(active_only=True) if include_imports else []
    return InboxPayload(
        items=items,
        completed_items=completed_items,
        failed_items=failed_items,
        import_jobs=jobs,
        completed_retention_days=COMPLETED_RETENTION_SECONDS // 86400,
        failed_retention_days=FAILED_RETENTION_SECONDS // 86400,
    )
