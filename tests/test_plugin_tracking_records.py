"""Clearing managed-plugin tracking rows never touches files on the game host."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.sql.dml import Delete

from services.plugins.tracking import forget_managed_plugin, forget_server_managed_plugins


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[object]:
        return list(self._rows)


class _Session:
    """Minimal AsyncSession stand-in that records what the helper asked for."""

    def __init__(self, rows: list[object] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        # Only the SELECT should yield rows; the bulk DELETE returns nothing.
        is_delete = isinstance(statement, Delete)
        return _Result([] if is_delete else self.rows)

    async def delete(self, item: object) -> None:
        self.deleted.append(item)

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_forget_one_deletes_only_the_matching_row():
    row = SimpleNamespace(id=3, server_id=1, display_name="MatchZy", source_type="market")
    session = _Session([row])

    removed = await forget_managed_plugin(session, 1, 3)

    assert removed is row
    assert session.deleted == [row]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_forget_one_returns_none_without_committing_when_absent():
    session = _Session([])

    assert await forget_managed_plugin(session, 1, 404) is None
    assert session.deleted == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_forget_all_reports_the_removed_count():
    rows = [SimpleNamespace(id=index) for index in range(3)]
    session = _Session(rows)

    assert await forget_server_managed_plugins(session, 1) == 3
    assert session.commits == 1
    # One SELECT to count, one bulk DELETE.
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_forget_all_skips_the_delete_when_nothing_is_tracked():
    session = _Session([])

    assert await forget_server_managed_plugins(session, 1) == 0
    assert session.commits == 0
    assert len(session.statements) == 1
