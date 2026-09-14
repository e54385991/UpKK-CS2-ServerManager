"""Overview dashboard aggregates that do not load full Server rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import Select
from sqlmodel import col, select

from modules.models.servers import Server, ServerStatus

OVERVIEW_SERVER_LIMIT = 1000
_ATTENTION_STATUSES = frozenset({ServerStatus.ERROR, ServerStatus.UNKNOWN})
_SECRET_COLUMNS = frozenset(
    {
        "ssh_password",
        "rcon_password",
        "server_password",
        "steam_account_token",
        "api_key",
    }
)


class OverviewDatabase(Protocol):
    async def execute(self, statement: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class OverviewServerStats:
    total: int
    running: int
    attention: int
    capacity: int


def overview_server_stats_statement(user_id: int) -> Select[Any]:
    """Same user scope and 1000-row cap as the previous ORM list, fewer columns."""
    return (
        select(col(Server.status), col(Server.max_players))
        .where(col(Server.user_id) == user_id)
        .limit(OVERVIEW_SERVER_LIMIT)
    )


def selected_column_names(statement: Select[Any]) -> tuple[str, ...]:
    return tuple(column.name for column in statement.selected_columns)


def statement_loads_secret_columns(statement: Select[Any]) -> bool:
    return bool(_SECRET_COLUMNS.intersection(selected_column_names(statement)))


async def load_overview_server_stats(
    session: OverviewDatabase, user_id: int
) -> OverviewServerStats:
    result = await session.execute(overview_server_stats_statement(user_id))
    running = 0
    attention = 0
    capacity = 0
    total = 0
    for status, max_players in result.all():
        total += 1
        if status == ServerStatus.RUNNING:
            running += 1
        if status in _ATTENTION_STATUSES:
            attention += 1
        capacity += int(max_players)
    return OverviewServerStats(
        total=total,
        running=running,
        attention=attention,
        capacity=capacity,
    )
