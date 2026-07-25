"""Steam.inf reads must stay bound to the owning application's SSH resources."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.auto_update_service import AutoUpdateService
from services.steam_inf_service import SteamInfService


@pytest.mark.asyncio
async def test_steam_inf_file_read_uses_injected_ssh_factory(monkeypatch) -> None:
    manager = SimpleNamespace(
        connect=AsyncMock(return_value=(True, "connected")),
        execute_command=AsyncMock(
            side_effect=[
                (True, "exists\n", ""),
                (True, "PatchVersion=1.42.3.4\n", ""),
            ]
        ),
        disconnect=AsyncMock(),
    )
    factory_calls = 0

    def create_manager():
        nonlocal factory_calls
        factory_calls += 1
        return manager

    def reject_global_manager():
        raise AssertionError("global SSHManager must not be constructed")

    monkeypatch.setattr("services.steam_inf_service.SSHManager", reject_global_manager)
    cache_set = AsyncMock()
    monkeypatch.setattr("services.steam_inf_service.redis_manager.set", cache_set)

    service = SteamInfService(ssh_manager_factory=create_manager)  # type: ignore[arg-type]
    server = SimpleNamespace(id=7, game_directory="/srv/cs2")

    success, version = await service.get_version_from_steam_inf(
        server,  # type: ignore[arg-type]
        force_refresh=True,
    )

    assert (success, version) == (True, "1.42.3.4")
    assert factory_calls == 1
    manager.connect.assert_awaited_once_with(server)
    manager.disconnect.assert_awaited_once_with()
    cache_set.assert_awaited_once_with(
        "steam_inf:version:7",
        "1.42.3.4",
        expire=service.CACHE_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_auto_update_initial_read_passes_injected_ssh_factory(monkeypatch) -> None:
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return None

        async def commit(self):
            return None

    def factory():
        return None

    captured_factories = []

    async def read_version(server, *, ssh_manager_factory=None):
        captured_factories.append(ssh_manager_factory)
        return True, "1.42.3.4"

    steam_service = SimpleNamespace(
        check_version=AsyncMock(return_value=(True, {"up_to_date": True}))
    )
    monkeypatch.setattr("modules.database.async_session_maker", Session)
    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.get_version_from_steam_inf",
        read_version,
    )

    service = AutoUpdateService(
        steam_service=steam_service,  # type: ignore[arg-type]
        ssh_manager_factory=factory,  # type: ignore[arg-type]
    )
    server = SimpleNamespace(id=9, current_game_version=None)

    await service._check_and_update_server(server)

    assert captured_factories == [factory]
    steam_service.check_version.assert_awaited_once_with("1.42.3.4")


@pytest.mark.asyncio
async def test_auto_update_verification_passes_injected_ssh_factory(monkeypatch) -> None:
    def factory():
        return None

    captured_factories = []

    async def refresh_version(server, *, ssh_manager_factory=None):
        captured_factories.append(ssh_manager_factory)
        return True, "1.42.3.4"

    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.refresh_version_cache",
        refresh_version,
    )

    service = AutoUpdateService(ssh_manager_factory=factory)  # type: ignore[arg-type]
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 0

    async def ignore_progress(_message):
        return None

    verified, observed, latest = await service._wait_for_updated_version(
        SimpleNamespace(id=11),
        required_version="1.42.3.4",
        log_progress=ignore_progress,
    )

    assert (verified, observed, latest) == (True, "1.42.3.4", None)
    assert captured_factories == [factory]
