"""Batch-query regressions for plugin market dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.plugin_market import (
    populate_dependency_details,
    validate_dependencies,
)
from modules import MarketPlugin


def _plugin(plugin_id: int, title: str, dependencies: str | None = None) -> MarketPlugin:
    return MarketPlugin(
        id=plugin_id,
        github_url=f"https://github.com/example/plugin-{plugin_id}",
        title=title,
        dependencies=dependencies,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_dependency_details_are_loaded_once_and_keep_declared_order(monkeypatch):
    first = _plugin(1, "First", "3,2,3")
    second = _plugin(4, "Second", "2")
    dependency_two = _plugin(2, "Two")
    dependency_three = _plugin(3, "Three")
    load = AsyncMock(return_value=[dependency_two, dependency_three])
    monkeypatch.setattr(MarketPlugin, "get_by_ids", load)
    db = object()

    responses = await populate_dependency_details(db, [first, second])

    load.assert_awaited_once_with(db, [3, 2, 3, 2])
    assert [item.id for item in responses[0].dependency_details] == [3, 2, 3]
    assert [item.id for item in responses[1].dependency_details] == [2]


@pytest.mark.asyncio
async def test_dependency_validation_batches_lookup_and_reports_first_missing(monkeypatch):
    load = AsyncMock(return_value=[_plugin(2, "Two")])
    monkeypatch.setattr(MarketPlugin, "get_by_ids", load)
    db = object()

    with pytest.raises(HTTPException) as caught:
        await validate_dependencies(db, [2, 9, 8])

    load.assert_awaited_once_with(db, [2, 9, 8])
    assert caught.value.status_code == 404
    assert caught.value.detail == "Dependency plugin with ID 9 not found"
