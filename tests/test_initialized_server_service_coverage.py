"""覆盖已初始化主机的持久化、兼容迁移和所有权边界。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from modules.models import InitializedServer
from services import initialized_server_service as service


class _Scalars:
    def __init__(self, rows: Iterable[InitializedServer]):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _Result:
    def __init__(self, rows: Iterable[InitializedServer]):
        self.rows = list(rows)

    def scalars(self):
        return _Scalars(self.rows)


class _DB:
    def __init__(self, rows=(), *, get_value=None, execute_error=None, commit_error=None):
        self.rows = list(rows)
        self.get_value = get_value
        self.execute_error = execute_error
        self.commit_error = commit_error
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        if self.execute_error:
            raise self.execute_error
        return _Result(self.rows)

    async def get(self, _model, _key):
        if isinstance(self.get_value, BaseException):
            raise self.get_value
        return self.get_value

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def refresh(self, row):
        if row.id is None:
            row.id = 900 + len(self.added)

    async def rollback(self):
        self.rollbacks += 1

    async def delete(self, row):
        self.deleted.append(row)


class _Legacy:
    def __init__(self, values=(), raw=None, *, error=None, delete_result=True):
        self.values = list(values)
        self.raw = raw
        self.error = error
        self.delete_result = delete_result
        self.deleted = []

    async def get_initialized_servers(self, _user_id):
        if self.error:
            raise self.error
        return list(self.values)

    async def get_initialized_server(self, _key):
        if self.error:
            raise self.error
        return self.raw

    async def delete_initialized_server(self, user_id, key):
        self.deleted.append((user_id, key))
        return self.delete_result

    async def set_initialized_server(self, *_args, **_kwargs):
        return "legacy-key"


def _row(*, row_id=1, user_id=7, created_at=None, updated_at=None, **overrides):
    values = dict(
        id=row_id,
        user_id=user_id,
        name="alpha",
        host="10.0.0.7",
        ssh_port=2222,
        ssh_user="steam",
        ssh_password="secret",
        game_directory="/srv/cs2",
        created_at=created_at,
        updated_at=updated_at,
    )
    values.update(overrides)
    return InitializedServer(**values)


def test_timestamp_and_record_converters_cover_defaults_and_timezone():
    assert (
        service._timestamp(datetime(2026, 1, 1))
        == datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    )
    assert service._timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)) > 0
    assert service._timestamp(12) == 12.0
    assert service._timestamp("bad") == 0.0

    record = service._record_from_database(
        _row(created_at=None, updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    )
    assert record.key == "1" and record.created_at > 0
    legacy = service._record_from_legacy({}, "legacy-key")
    assert legacy.key == "legacy-key"
    assert legacy.ssh_port == 22 and legacy.game_directory.endswith("/cs2")
    assert service._identity(record) == (
        "10.0.0.7",
        2222,
        "steam",
        "/srv/cs2",
    )


@pytest.mark.asyncio
async def test_list_uses_durable_rows_and_imports_unique_legacy_records():
    durable = _row(row_id=2, host="same")
    legacy = _Legacy(
        [
            {"key": "old-1", "user_id": 7, "name": "new", "host": "new-host"},
            {
                "key": "same-key",
                "user_id": 7,
                "host": "same",
                "ssh_port": 2222,
                "ssh_user": "steam",
                "game_directory": "/srv/cs2",
            },
            {"key": "other", "user_id": 8, "host": "ignored"},
        ]
    )
    db = _DB([durable])

    result = await service.list_initialized_servers(db, 7, legacy_store=legacy)

    assert [item.host for item in result] == ["new-host", "same"]
    assert len(db.added) == 1 and db.commits == 1
    assert legacy.deleted == [(7, "old-1")]


@pytest.mark.asyncio
async def test_list_falls_back_when_database_or_legacy_reads_fail():
    legacy_record = {"key": "old", "user_id": 7, "host": "legacy"}
    db_error = _DB(execute_error=RuntimeError("db down"))
    legacy = _Legacy([legacy_record])
    assert (await service.list_initialized_servers(db_error, 7, legacy_store=legacy))[
        0
    ].host == "legacy"

    durable = _row(host="durable")
    legacy_error = _Legacy(error=RuntimeError("redis down"))
    assert (await service.list_initialized_servers(_DB([durable]), 7, legacy_store=legacy_error))[
        0
    ].host == "durable"

    no_legacy = _Legacy([])
    assert (await service.list_initialized_servers(_DB([durable]), 7, legacy_store=no_legacy))[
        0
    ].host == "durable"


@pytest.mark.asyncio
async def test_list_returns_legacy_records_when_import_commit_fails():
    legacy_record = {"key": "old", "user_id": 7, "host": "legacy"}
    db = _DB([], commit_error=RuntimeError("commit failed"))

    result = await service.list_initialized_servers(db, 7, legacy_store=_Legacy([legacy_record]))

    assert [item.host for item in result] == ["legacy"]
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_save_initialized_server_inserts_and_updates_by_identity():
    db = _DB([])
    key = await service.save_initialized_server(
        db,
        user_id=7,
        name="new",
        host="host",
        ssh_port=22,
        ssh_user="steam",
        ssh_password="pw",
        game_directory="/cs2",
    )
    assert key == "901" and db.added[0].name == "new"

    existing = _row(row_id=3, host="host", ssh_port=22, ssh_user="steam", game_directory="/cs2")
    db = _DB([existing])
    key = await service.save_initialized_server(
        db,
        user_id=7,
        name="renamed",
        host="host",
        ssh_port=22,
        ssh_user="steam",
        ssh_password="new-pw",
        game_directory="/cs2",
    )
    assert key == "3" and existing.name == "renamed" and existing.ssh_password == "new-pw"
    assert existing.created_at is not None and existing.updated_at == existing.created_at


@pytest.mark.asyncio
async def test_resolve_enforces_owner_and_handles_numeric_and_legacy_keys():
    row = _row(row_id=4, user_id=7)
    db = _DB(get_value=row)
    resolved = await service.resolve_initialized_server(db, "4", 7, legacy_store=_Legacy())
    assert resolved is not None and resolved.database_record is row

    with pytest.raises(service.InitializedServerAccessDenied):
        await service.resolve_initialized_server(_DB(get_value=_row(user_id=8)), "4", 7)

    legacy = _Legacy(raw={"user_id": 7, "host": "legacy"})
    resolved = await service.resolve_initialized_server(
        _DB(get_value=None), "not-numeric", 7, legacy_store=legacy
    )
    assert resolved is not None and resolved.legacy_key == "not-numeric"

    with pytest.raises(service.InitializedServerAccessDenied):
        await service.resolve_initialized_server(
            _DB(get_value=None),
            "legacy",
            7,
            legacy_store=_Legacy(raw={"user_id": 8}),
        )
    assert (
        await service.resolve_initialized_server(
            _DB(get_value=None), "missing", 7, legacy_store=_Legacy(raw=None)
        )
        is None
    )


@pytest.mark.asyncio
async def test_resolve_database_errors_and_delete_paths():
    legacy = _Legacy(raw={"user_id": 7, "host": "fallback"})
    resolved = await service.resolve_initialized_server(
        _DB(get_value=RuntimeError("db")), "12", 7, legacy_store=legacy
    )
    assert resolved is not None and resolved.record.host == "fallback"
    assert (
        await service.resolve_initialized_server(
            _DB(get_value=None), "12", 7, legacy_store=_Legacy(error=RuntimeError("redis"))
        )
        is None
    )

    db = _DB(get_value=_row(row_id=8, user_id=7))
    assert await service.delete_initialized_server(db, "8", 7) is True
    assert db.deleted and db.commits == 1

    legacy = _Legacy(raw={"user_id": 7, "host": "legacy"}, delete_result=True)
    assert (
        await service.delete_initialized_server(
            _DB(get_value=None), "legacy", 7, legacy_store=legacy
        )
        is True
    )
    assert legacy.deleted == [(7, "legacy")]
    assert (
        await service.delete_initialized_server(
            _DB(get_value=None), "missing", 7, legacy_store=_Legacy(raw=None)
        )
        is False
    )


@pytest.mark.asyncio
async def test_delete_batches_only_owned_rows_and_handles_empty_result():
    assert await service.delete_initialized_servers(_DB(), [], 7) == 0
    first = _row(row_id=1, user_id=7)
    second = _row(row_id=2, user_id=7)
    db = _DB([first, second])
    assert await service.delete_initialized_servers(db, [1, 2, 999], 7) == 2
    assert db.deleted == [first, second] and db.commits == 1

    db = _DB([])
    assert await service.delete_initialized_servers(db, [8], 7) == 0
    assert db.commits == 0


def test_store_default_prefers_explicit_store():
    explicit = SimpleNamespace()
    assert service._store_or_default(explicit) is explicit
