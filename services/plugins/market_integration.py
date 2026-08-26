"""Injected bridge between GitHub plans and market installation workflows."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import Server, User

BuildMarketPlan = Callable[..., Awaitable[dict[str, Any]]]
ExecuteMarketPlan = Callable[..., Awaitable[dict[str, Any]]]

_build_market_plan: BuildMarketPlan | None = None
_execute_market_plan: ExecuteMarketPlan | None = None


def configure_market_plan_handlers(
    build: BuildMarketPlan,
    execute: ExecuteMarketPlan,
) -> None:
    """Install domain handlers without creating a reverse module dependency."""
    global _build_market_plan, _execute_market_plan
    _build_market_plan = build
    _execute_market_plan = execute


async def build_market_plan(
    db: AsyncSession,
    server_id: int,
    plugin_id: int,
    *,
    server: Server,
) -> dict[str, Any]:
    if _build_market_plan is None:
        raise RuntimeError("Plugin market planning service is not configured")
    return await _build_market_plan(db, server_id, plugin_id, server=server)


async def execute_market_plan(
    db: AsyncSession,
    server: Server,
    user: User,
    plugin_id: int,
    acknowledged_warning_rule_ids: set[int],
    *,
    expected_plan_hash: str,
    acquire_lock: bool,
    operation_id: str | None,
) -> dict[str, Any]:
    if _execute_market_plan is None:
        raise RuntimeError("Plugin market execution service is not configured")
    return await _execute_market_plan(
        db,
        server,
        user,
        plugin_id,
        acknowledged_warning_rule_ids,
        expected_plan_hash=expected_plan_hash,
        acquire_lock=acquire_lock,
        operation_id=operation_id,
    )
