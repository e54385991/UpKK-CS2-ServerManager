"""Overview summary uses a column projection and the existing 1000-row cap."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from modules.models.servers import ServerStatus
from services.overview.summary import (
    OVERVIEW_SERVER_LIMIT,
    load_overview_server_stats,
    overview_server_stats_statement,
    selected_column_names,
    statement_loads_secret_columns,
)


def test_overview_statement_selects_only_status_and_capacity_columns():
    statement = overview_server_stats_statement(7)
    assert selected_column_names(statement) == ("status", "max_players")
    assert statement_loads_secret_columns(statement) is False
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "user_id" in sql
    assert "limit 1000" in sql.replace("\n", " ")
    assert "ssh_password" not in sql
    assert "rcon_password" not in sql
    assert "server_password" not in sql


async def test_overview_stats_match_previous_python_totals():
    rows = [
        (ServerStatus.RUNNING, 16),
        (ServerStatus.ERROR, 10),
        (ServerStatus.UNKNOWN, 8),
        (ServerStatus.STOPPED, 32),
    ]

    class _Session:
        async def execute(self, statement):
            del statement
            return SimpleNamespace(all=lambda: list(rows))

    stats = await load_overview_server_stats(_Session(), user_id=3)
    assert stats.total == 4
    assert stats.running == 1
    assert stats.attention == 2
    assert stats.capacity == 66


def test_overview_stats_preserve_the_thousand_row_cap_in_sql():
    assert OVERVIEW_SERVER_LIMIT == 1000
    sql = str(
        overview_server_stats_statement(1).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LIMIT 1000" in sql
