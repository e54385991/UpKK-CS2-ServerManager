"""Detached monitoring queries and their public response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cs2_manager.core import Principal
from modules.models import Server


class ServerNotFoundError(LookupError):
    """The requested server is absent or not visible to the principal."""


@dataclass(frozen=True, slots=True)
class ServerA2STarget:
    """Minimal server data safe to retain after the database phase."""

    id: int
    query_host: str
    query_port: int


class ServerMonitoringRepository:
    """Read-only server queries; transaction completion belongs to the UoW."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def visible_server_ids(self, principal: Principal) -> list[int]:
        columns = cast(Any, Server).__table__.c
        statement = select(columns.id)
        if not principal.is_admin:
            statement = statement.where(columns.user_id == principal.id)
        result = await self._session.execute(statement)
        return [int(server_id) for server_id in result.scalars().all()]

    async def require_a2s_target(
        self,
        server_id: int,
        principal: Principal,
    ) -> ServerA2STarget:
        columns = cast(Any, Server).__table__.c
        statement = select(
            columns.id,
            columns.host,
            columns.game_port,
            columns.a2s_query_host,
            columns.a2s_query_port,
        ).where(columns.id == server_id)
        if not principal.is_admin:
            statement = statement.where(columns.user_id == principal.id)
        result = await self._session.execute(statement)
        row = result.one_or_none()
        if row is None:
            raise ServerNotFoundError("Server not found")
        values = row._mapping
        return ServerA2STarget(
            id=int(values["id"]),
            query_host=str(values["a2s_query_host"] or values["host"]),
            query_port=int(values["a2s_query_port"] or values["game_port"]),
        )


class MonitoringLogResponse(BaseModel):
    """One Redis-backed monitoring event."""

    id: int
    server_id: int
    event_type: str
    status: str
    message: str
    created_at: str


class PingResponse(BaseModel):
    status: str
    message: str


class PublicPingResponse(PingResponse):
    public: bool


class A2STestResponse(PingResponse):
    timestamp: str
    admin: bool


class A2SCacheDebug(BaseModel):
    endpoint: str
    router: str | None = None
    version: str
    user_id: int
    authenticated: bool


class A2SCacheEnvelope(BaseModel):
    """Owner-filtered A2S cache data with compatibility diagnostics."""

    servers: dict[str, dict[str, Any]]
    timestamp: str
    debug: A2SCacheDebug
    steam_latest_version: str | None = None
    error: str | None = None


class A2SQueryResponse(BaseModel):
    """Live A2S response after the database target has been detached."""

    query_host: str
    query_port: int = Field(ge=1, le=65535)
    success: bool
    server_info: dict[str, Any] | None = None
    players: list[dict[str, Any]]
    timestamp: str
