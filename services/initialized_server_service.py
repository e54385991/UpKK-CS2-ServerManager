"""Persistent, owner-scoped storage for hosts completed by the setup wizard."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlmodel import col, select

from modules.models import InitializedServer
from modules.utils import get_current_time
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)


class LegacyInitializedServerStore(Protocol):
    """The small Redis surface needed to read and retire legacy records."""

    async def get_initialized_servers(self, user_id: int) -> list[dict[str, object]]: ...

    async def get_initialized_server(self, server_key: str) -> dict[str, object] | None: ...

    async def delete_initialized_server(self, user_id: int, server_key: str) -> bool: ...

    async def set_initialized_server(
        self, user_id: int, server_data: dict[str, object], expire: int | None = None
    ) -> str: ...


class InitializedServerAccessDenied(Exception):
    """Raised when a saved host belongs to another panel user."""


@dataclass(frozen=True, slots=True)
class InitializedServerRecord:
    """Transport-independent projection of a saved initialized host."""

    key: str
    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: float


@dataclass(frozen=True, slots=True)
class _ResolvedRecord:
    record: InitializedServerRecord
    database_record: InitializedServer | None = None
    legacy_key: str | None = None


def _timestamp(value: object) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _integer(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _record_from_database(value: InitializedServer) -> InitializedServerRecord:
    return InitializedServerRecord(
        key=str(value.id),
        user_id=value.user_id,
        name=value.name,
        host=value.host,
        ssh_port=value.ssh_port,
        ssh_user=value.ssh_user,
        ssh_password=value.ssh_password,
        game_directory=value.game_directory,
        created_at=_timestamp(value.created_at) or _timestamp(value.updated_at),
    )


def _record_from_legacy(
    value: dict[str, object], fallback_key: str = ""
) -> InitializedServerRecord:
    return InitializedServerRecord(
        key=str(value.get("key") or fallback_key),
        user_id=_integer(value.get("user_id"), 0),
        name=str(value.get("name") or ""),
        host=str(value.get("host") or ""),
        ssh_port=_integer(value.get("ssh_port"), 22),
        ssh_user=str(value.get("ssh_user") or ""),
        ssh_password=str(value.get("ssh_password") or ""),
        game_directory=str(value.get("game_directory") or "/home/cs2server/cs2"),
        created_at=_timestamp(value.get("created_at")),
    )


def _identity(value: InitializedServerRecord) -> tuple[str, int, str, str]:
    return value.host, value.ssh_port, value.ssh_user, value.game_directory


def _newest_first(records: list[InitializedServerRecord]) -> list[InitializedServerRecord]:
    return sorted(records, key=lambda item: (item.created_at, item.key), reverse=True)


def _store_or_default(store: LegacyInitializedServerStore | None) -> LegacyInitializedServerStore:
    return store or redis_manager


def _legacy_list_belongs_to_user(value: dict[str, object], user_id: int) -> bool:
    """Trust the legacy list's owner scope when old entries lack user_id."""
    raw_user_id = value.get("user_id")
    return raw_user_id is None or _integer(raw_user_id, 0) == user_id


async def _list_database(
    db, user_id: int
) -> tuple[list[InitializedServerRecord], list[InitializedServer], bool]:
    """Read the durable store, returning availability separately for compatibility fallback."""
    try:
        result = await db.execute(
            select(InitializedServer)
            .where(InitializedServer.user_id == user_id)
            .order_by(col(InitializedServer.created_at).desc(), col(InitializedServer.id).desc())
        )
        rows = list(result.scalars().all())
        return [_record_from_database(row) for row in rows], rows, True
    except Exception as exc:
        logger.warning("Unable to read initialized server records from database: %s", exc)
        return [], [], False


async def list_initialized_servers(
    db,
    user_id: int,
    *,
    legacy_store: LegacyInitializedServerStore | None = None,
) -> list[InitializedServerRecord]:
    """List durable records and import compatible Redis records when possible."""
    durable, durable_rows, database_available = await _list_database(db, user_id)
    store = _store_or_default(legacy_store)
    try:
        legacy_values = await store.get_initialized_servers(user_id)
    except Exception as exc:
        logger.warning("Unable to read legacy initialized server records: %s", exc)
        return durable

    legacy_records = [
        _record_from_legacy(value)
        for value in legacy_values
        if _legacy_list_belongs_to_user(value, user_id)
    ]
    if not database_available or not legacy_records:
        return _newest_first(durable or legacy_records)

    known = {_identity(value) for value in durable}
    pending: list[tuple[InitializedServer, InitializedServerRecord]] = []
    for legacy in legacy_records:
        if _identity(legacy) in known:
            continue
        row = InitializedServer(
            user_id=user_id,
            name=legacy.name,
            host=legacy.host,
            ssh_port=legacy.ssh_port,
            ssh_user=legacy.ssh_user,
            ssh_password=legacy.ssh_password,
            game_directory=legacy.game_directory,
        )
        db.add(row)
        pending.append((row, legacy))
        known.add(_identity(legacy))

    if pending:
        try:
            await db.commit()
            imported: list[InitializedServerRecord] = []
            for row, legacy in pending:
                await db.refresh(row)
                imported.append(_record_from_database(row))
                if legacy.key:
                    await store.delete_initialized_server(user_id, legacy.key)
            durable = imported + durable
        except Exception as exc:
            await db.rollback()
            logger.warning("Unable to migrate legacy initialized server records: %s", exc)
            return _newest_first(durable + legacy_records)

    return _newest_first(durable)


async def save_initialized_server(
    db,
    *,
    user_id: int,
    name: str,
    host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_password: str,
    game_directory: str,
) -> str:
    """Upsert one owner-scoped setup result and return its durable key."""
    result = await db.execute(
        select(InitializedServer)
        .where(
            InitializedServer.user_id == user_id,
            InitializedServer.host == host,
            InitializedServer.ssh_port == ssh_port,
            InitializedServer.ssh_user == ssh_user,
            InitializedServer.game_directory == game_directory,
        )
        .order_by(col(InitializedServer.id).desc())
    )
    row = result.scalars().first()
    if row is None:
        row = InitializedServer(
            user_id=user_id,
            name=name,
            host=host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            game_directory=game_directory,
        )
    else:
        row.name = name
        row.ssh_password = ssh_password
        # Re-saving a host is a fresh successful initialization. Keep the
        # durable list ordered by the most recent setup rather than the first
        # time this host was ever saved.
        row.created_at = get_current_time()
        row.updated_at = row.created_at
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return str(row.id)


async def resolve_initialized_server(
    db,
    key: str,
    user_id: int,
    *,
    legacy_store: LegacyInitializedServerStore | None = None,
) -> _ResolvedRecord | None:
    """Resolve a durable ID or a legacy Redis key and enforce owner scope."""
    store = _store_or_default(legacy_store)
    if key.isdecimal():
        try:
            row = await db.get(InitializedServer, int(key))
        except Exception as exc:
            logger.warning("Unable to read initialized server %s from database: %s", key, exc)
            row = None
        if row is not None:
            if row.user_id != user_id:
                raise InitializedServerAccessDenied
            return _ResolvedRecord(_record_from_database(row), database_record=row)

    try:
        raw = await store.get_initialized_server(key)
    except Exception as exc:
        logger.warning("Unable to read legacy initialized server %s: %s", key, exc)
        return None
    if not raw:
        return None
    record = _record_from_legacy(raw, key)
    if record.user_id != user_id:
        raise InitializedServerAccessDenied
    return _ResolvedRecord(record, legacy_key=key)


async def delete_initialized_server(
    db,
    key: str,
    user_id: int,
    *,
    legacy_store: LegacyInitializedServerStore | None = None,
) -> bool:
    """Delete one saved host after checking that it belongs to the caller."""
    resolved = await resolve_initialized_server(db, key, user_id, legacy_store=legacy_store)
    if resolved is None:
        return False
    if resolved.database_record is not None:
        await db.delete(resolved.database_record)
        await db.commit()
        return True
    if resolved.legacy_key is None:
        return False
    return await _store_or_default(legacy_store).delete_initialized_server(
        user_id, resolved.legacy_key
    )


async def delete_initialized_servers(db, ids: list[int], user_id: int) -> int:
    """Delete several durable hosts in one owner-scoped transaction."""
    if not ids:
        return 0
    result = await db.execute(
        select(InitializedServer).where(
            InitializedServer.user_id == user_id,
            col(InitializedServer.id).in_(set(ids)),
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        await db.delete(row)
    if rows:
        await db.commit()
    return len(rows)


__all__ = [
    "InitializedServerAccessDenied",
    "InitializedServerRecord",
    "LegacyInitializedServerStore",
    "delete_initialized_server",
    "delete_initialized_servers",
    "list_initialized_servers",
    "resolve_initialized_server",
    "save_initialized_server",
]
