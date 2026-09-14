"""Load isolated users, servers, plugins, and Redis operation history."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import SQLModel

from modules.auth import create_access_token, get_password_hash
from modules.database import async_session_maker, engine
from modules.database_migrations import upgrade_database
from modules.models import AuthType, MarketPlugin, Server, User
from modules.models.plugins import PluginCategory, PluginFramework
from modules.models.servers import ServerStatus
from scripts.perf.env import PERF_PASSWORD, assert_isolated_target
from scripts.perf.profiles import (
    FLEETS,
    MARKETS,
    USERNAMES,
    FleetName,
    HistoryKind,
    MarketName,
    event_count,
    history_kind_for_index,
    retained_counts,
    split_ownership,
)
from services.redis_manager import redis_manager
from services.server_operation_history import (
    COMPLETED_RETENTION_SECONDS,
    FAILED_RETENTION_SECONDS,
)

CATEGORIES = tuple(PluginCategory)
FRAMEWORKS = (
    PluginFramework.COUNTERSTRIKESHARP,
    PluginFramework.COUNTERSTRIKESHARP,
    PluginFramework.COUNTERSTRIKESHARP,
    PluginFramework.SWIFTLY,
    PluginFramework.OTHER,
)


def plugin_values(index: int, now: datetime) -> dict[str, Any]:
    return {
        "github_url": f"https://github.com/perf-fixture/plugin-{index}",
        "title": f"Perf Plugin {index}",
        "description": "Isolated marketplace fixture",
        "author": "perf",
        "version": "1.0.0",
        "category": CATEGORIES[index % len(CATEGORIES)],
        "framework": FRAMEWORKS[index % len(FRAMEWORKS)],
        "is_recommended": index % 17 == 0,
        "install_count": max(0, 5000 - index),
        "download_count": max(0, 8000 - index),
        "created_at": now - timedelta(days=index % 400),
    }


def server_status_for(index: int) -> ServerStatus:
    remainder = index % 10
    if remainder == 0:
        return ServerStatus.ERROR
    if remainder in {1, 2}:
        return ServerStatus.STOPPED
    return ServerStatus.RUNNING


def operation_record(
    *,
    server_id: int,
    actor_id: int,
    status: str,
    index: int,
    now: datetime,
) -> dict[str, Any]:
    operation_id = str(uuid.uuid4())
    started = now - timedelta(minutes=index + 1)
    completed = started + timedelta(seconds=30) if status in {"completed", "failed"} else None
    return {
        "operation_id": operation_id,
        "server_id": server_id,
        "action": "install_plugin",
        "command": f"perf {status} {index}",
        "status": status,
        "success": status == "completed",
        "message": f"{status} fixture {index}",
        "server_status": None,
        "actor_user_id": actor_id,
        "started_at": started.isoformat(),
        "execution_started_at": started.isoformat() if status != "queued" else None,
        "completed_at": completed.isoformat() if completed else None,
    }


def operation_events(
    record: dict[str, Any], kind: HistoryKind, status: str
) -> list[dict[str, Any]]:
    count = event_count(kind, status)
    events: list[dict[str, Any]] = []
    for index in range(count):
        events.append(
            {
                "type": "progress" if index < count - 1 else "status",
                "message": f"{record['command']} event {index}",
                "ts": record["started_at"],
            }
        )
    return events


async def run_seed(
    *,
    fleet_name: FleetName,
    market_name: MarketName,
    history: str,
    settings: object,
) -> dict[str, Any]:
    assert_isolated_target(settings)
    fleet = FLEETS[fleet_name if fleet_name in FLEETS else "fleet-10"]
    market = MARKETS[market_name if market_name in MARKETS else "market-100"]
    await upgrade_database(engine)
    hashed = get_password_hash(PERF_PASSWORD)
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        await _truncate(session)
        users = await _insert_users(session, hashed)
        owners = split_ownership(fleet.servers)
        servers = await _insert_servers(session, users, owners, now)
        await _insert_plugins(session, market.plugins, now)
        await session.commit()
    await redis_manager.client.flushdb()
    await _insert_operations(servers, users, history, now)
    return {
        "fleet": fleet.name,
        "servers": fleet.servers,
        "ownership": owners.__dict__,
        "plugins": market.plugins,
        "history": history,
        "users": {name: {"id": user.id, "is_admin": user.is_admin} for name, user in users.items()},
        "tokens": {
            name: create_access_token({"sub": str(user.id)}) for name, user in users.items()
        },
    }


async def _truncate(session: Any) -> None:
    names = [table.name for table in reversed(SQLModel.metadata.sorted_tables)]
    quoted = ", ".join(f'"{name}"' for name in names)
    await session.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


async def _insert_users(session: Any, hashed: str) -> dict[str, User]:
    users: dict[str, User] = {}
    for name in USERNAMES:
        user = User(
            username=name,
            email=f"{name}@perf.invalid",
            hashed_password=hashed,
            is_admin=name == "admin",
            is_active=True,
        )
        session.add(user)
        users[name] = user
    await session.flush()
    return users


async def _insert_servers(
    session: Any,
    users: dict[str, User],
    owners: Any,
    now: datetime,
) -> list[Server]:
    created: list[Server] = []
    sequence = _owner_sequence(owners)
    for index, owner_name in enumerate(sequence):
        server = Server(
            user_id=users[owner_name].id,
            name=f"perf-server-{index + 1}",
            host=f"perf-{index + 1}.invalid",
            ssh_user="steam",
            auth_type=AuthType.PASSWORD,
            ssh_password="unused",
            status=server_status_for(index),
            enable_panel_monitoring=False,
            enable_a2s_monitoring=False,
            enable_auto_update=False,
            created_at=now,
            updated_at=now,
        )
        session.add(server)
        created.append(server)
    await session.flush()
    return created


def _owner_sequence(owners: Any) -> list[str]:
    sequence: list[str] = []
    sequence.extend(["admin"] * owners.admin)
    sequence.extend(["member"] * owners.member)
    sequence.extend(["tenant_a"] * owners.tenant_a)
    sequence.extend(["tenant_b"] * owners.tenant_b)
    return sequence


async def _insert_plugins(session: Any, count: int, now: datetime) -> None:
    batch: list[MarketPlugin] = []
    for index in range(count):
        batch.append(MarketPlugin(**plugin_values(index, now)))
        if len(batch) >= 500:
            session.add_all(batch)
            await session.flush()
            batch = []
    if batch:
        session.add_all(batch)
        await session.flush()


async def _insert_operations(
    servers: list[Server],
    users: dict[str, User],
    history: str,
    now: datetime,
) -> None:
    actor_id = int(users["admin"].id or 1)
    for index, server in enumerate(servers):
        kind = history_kind_for_index(index, len(servers), _history(history))
        await _seed_server_history(int(server.id), actor_id, kind, now)


def _history(value: str) -> HistoryKind:
    if value in {"empty", "daily", "max"}:
        return value
    return "daily"


async def _seed_server_history(
    server_id: int, actor_id: int, kind: HistoryKind, now: datetime
) -> None:
    running, queued, completed, failed = retained_counts(kind)
    current_id = None
    pending: list[str] = []
    completed_ids: list[str] = []
    failed_ids: list[str] = []
    if running:
        record = operation_record(
            server_id=server_id, actor_id=actor_id, status="running", index=0, now=now
        )
        await _persist_record(record, kind, 86400)
        current_id = str(record["operation_id"])
    for index in range(queued):
        record = operation_record(
            server_id=server_id, actor_id=actor_id, status="queued", index=index + 1, now=now
        )
        await _persist_record(record, kind, 86400)
        pending.append(str(record["operation_id"]))
    for index in range(completed):
        record = operation_record(
            server_id=server_id, actor_id=actor_id, status="completed", index=index, now=now
        )
        await _persist_record(record, kind, COMPLETED_RETENTION_SECONDS)
        completed_ids.append(str(record["operation_id"]))
    for index in range(failed):
        record = operation_record(
            server_id=server_id, actor_id=actor_id, status="failed", index=index, now=now
        )
        await _persist_record(record, kind, FAILED_RETENTION_SECONDS)
        failed_ids.append(str(record["operation_id"]))
    if current_id:
        await redis_manager.set(f"server_op_current:{server_id}", current_id, expire=86400)
    await redis_manager.set(f"server_op_pending:{server_id}", pending, expire=86400)
    await redis_manager.set(
        f"server_op_completed:{server_id}", completed_ids, expire=COMPLETED_RETENTION_SECONDS
    )
    await redis_manager.set(
        f"server_op_failed:{server_id}", failed_ids, expire=FAILED_RETENTION_SECONDS
    )


async def _persist_record(record: dict[str, Any], kind: HistoryKind, expire: int) -> None:
    operation_id = str(record["operation_id"])
    await redis_manager.set(f"server_op:{operation_id}", record, expire=expire)
    events = operation_events(record, kind, str(record["status"]))
    if not events:
        return
    key = redis_manager.prefixed_key(f"server_op:{operation_id}:events")
    pipeline = redis_manager.client.pipeline(transaction=False)
    for event in events:
        pipeline.rpush(key, json.dumps(event, ensure_ascii=False))
    pipeline.ltrim(key, -300, -1)
    pipeline.expire(key, expire)
    await pipeline.execute()
