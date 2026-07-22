"""Route-level behavior for plugin configuration source and save APIs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.routes import plugin_configs


class _LockContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _LockService:
    def get(self, *_args, **_kwargs):
        return _LockContext()


@pytest.mark.asyncio
async def test_cross_server_source_id_is_not_returned():
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(HTTPException) as exc:
        await plugin_configs._source_for_server(db, server_id=10, source_id=99)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_removing_default_source_soft_disables_it(monkeypatch):
    source = SimpleNamespace(is_default=True, is_enabled=True)
    db = SimpleNamespace(add=Mock(), delete=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(
        plugin_configs, "_source_for_server", AsyncMock(return_value=source)
    )

    response = await plugin_configs.delete_source(
        server_id=1, source_id=2, db=db, current_user=SimpleNamespace()
    )

    assert response == {"success": True}
    assert source.is_enabled is False
    db.add.assert_called_once_with(source)
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_rejects_stale_revision_without_writing(monkeypatch):
    server = SimpleNamespace(game_directory="/home/cs2")
    source = SimpleNamespace(
        source_type="file", relative_path="cs2/game/csgo/cfg/plugin.cfg"
    )
    manager = SimpleNamespace(disconnect=AsyncMock())
    atomic_write = AsyncMock()
    monkeypatch.setattr(
        plugin_configs, "get_server_with_permission", AsyncMock(return_value=server)
    )
    monkeypatch.setattr(
        plugin_configs, "_source_for_server", AsyncMock(return_value=source)
    )
    monkeypatch.setattr(plugin_configs, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(
        plugin_configs, "read_text_file", AsyncMock(return_value="setting 1\n")
    )
    monkeypatch.setattr(plugin_configs, "atomic_write_text_file", atomic_write)
    monkeypatch.setattr(plugin_configs, "maintenance_lock_service", _LockService())
    request = plugin_configs.ConfigSaveRequest(
        path=source.relative_path,
        expected_revision="0" * 64,
        mode="visual",
        changes=[],
    )

    with pytest.raises(HTTPException) as exc:
        await plugin_configs.save_config_file(
            server_id=1,
            source_id=2,
            request=request,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    atomic_write.assert_not_awaited()
    manager.disconnect.assert_awaited_once()

