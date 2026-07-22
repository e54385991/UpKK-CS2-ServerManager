"""Auto-update completion must be gated by a fresh steam.inf read."""

import pytest

from modules.models import AuthType, DeploymentLog, Server
from services.auto_update_service import AutoUpdateService


class FakeDatabaseState:
    def __init__(self):
        self.log = None
        self.server_updates = 0


class FakeSession:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, value):
        if isinstance(value, DeploymentLog):
            value.id = 1
            self.state.log = value

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    async def get(self, model, object_id):
        if model is DeploymentLog:
            return self.state.log
        return None

    async def execute(self, statement):
        self.state.server_updates += 1
        return None


class SuccessfulSSHManager:
    async def update_server(self, server, progress_callback=None):
        if progress_callback:
            await progress_callback("SteamCMD finished")
        return True, "Server restored successfully"


class SteamCMDReportedFailureSSHManager:
    async def update_server(self, server, progress_callback=None):
        if progress_callback:
            await progress_callback("steamcmd.sh started")
        return (
            False,
            "SteamCMD update failed: steamcmd.sh[26858]: Starting "
            "/home/cs2server/cs2ze/steamcmd/linux32/steamcmd; "
            "recovery start succeeded: Server started successfully",
        )


class NonSteamCMDFailureSSHManager:
    async def update_server(self, server, progress_callback=None):
        return False, "Connection failed: SSH host is unavailable"


class SteamCMDRecoveryFailureSSHManager:
    async def update_server(self, server, progress_callback=None):
        return (
            False,
            "SteamCMD update failed: network unavailable; "
            "recovery start failed: Server did not start",
        )


def make_server():
    return Server(
        id=77,
        user_id=1,
        name="Verifier",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )


def install_trigger_fakes(
    monkeypatch,
    state,
    manager_class,
    refresh_version,
    steam_check,
    sleep=None,
):
    monkeypatch.setattr("modules.database.async_session_maker", lambda: FakeSession(state))
    monkeypatch.setattr("services.auto_update_service.SSHManager", manager_class)
    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.refresh_version_cache",
        refresh_version,
    )
    monkeypatch.setattr(
        "services.auto_update_service.steam_api_service.check_version",
        steam_check,
    )
    if sleep is not None:
        monkeypatch.setattr("services.auto_update_service.asyncio.sleep", sleep)

    notifications = []

    def capture(*args, **kwargs):
        notifications.append(
            {
                "title": kwargs.get("title"),
                "details": kwargs.get("details"),
                "state": kwargs.get("state"),
                "success": args[3],
                "message": args[4],
            }
        )
        return True

    monkeypatch.setattr(
        "services.auto_update_service.discord_notification_service.queue_notify",
        capture,
    )
    return notifications


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observed,steam_up_to_date,expected_success",
    [("1.41.6.9", False, True), ("1.41.6.8", False, False), ("1.41.7.0", True, True)],
)
async def test_terminal_notification_depends_on_steam_inf(
    monkeypatch, observed, steam_up_to_date, expected_success
):
    state = FakeDatabaseState()

    async def refresh_version(server):
        return True, observed

    async def final_steam_check(version):
        return True, {
            "up_to_date": steam_up_to_date,
            "required_version": "1.41.7.0" if steam_up_to_date else "1.41.6.9",
        }

    notifications = install_trigger_fakes(
        monkeypatch,
        state,
        SuccessfulSSHManager,
        refresh_version,
        final_steam_check,
    )
    service = AutoUpdateService()
    service.VERSION_VERIFICATION_TIMEOUT_SECONDS = 0
    await service._trigger_server_update(
        make_server(),
        current_version="1.41.6.8",
        required_version="1.41.6.9",
        version_source="steam.inf",
    )

    assert notifications[0]["title"] == "Automatic update started"
    assert notifications[0]["state"] == "in_progress"
    assert notifications[-1]["details"]["Observed Version"] == observed
    assert len(notifications) == 2
    assert notifications[-1]["title"] == (
        "Automatic update completed" if expected_success else "Automatic update failed"
    )
    assert state.log.status == ("success" if expected_success else "failed")


@pytest.mark.asyncio
async def test_steamcmd_reported_failure_is_reconciled_when_version_catches_up(monkeypatch):
    state = FakeDatabaseState()

    sleep_calls = []

    async def capture_sleep(delay):
        sleep_calls.append(delay)

    observed_versions = iter(("1.41.6.9", "1.41.7.0"))
    refresh_calls = 0

    async def refresh_version(server):
        nonlocal refresh_calls
        refresh_calls += 1
        return True, next(observed_versions)

    async def final_steam_check(version):
        return True, {"up_to_date": False, "required_version": "1.41.7.0"}

    notifications = install_trigger_fakes(
        monkeypatch,
        state,
        SteamCMDReportedFailureSSHManager,
        refresh_version,
        final_steam_check,
        capture_sleep,
    )

    service = AutoUpdateService()
    await service._trigger_server_update(
        make_server(),
        current_version="1.41.6.9",
        required_version="1.41.7.0",
        version_source="steam.inf",
    )

    assert sleep_calls == [30]
    assert refresh_calls == 2
    assert [item["title"] for item in notifications] == [
        "Automatic update started",
        "Automatic update completed",
    ]
    assert notifications[-1]["details"]["Observed Version"] == "1.41.7.0"
    assert notifications[-1]["success"] is True
    assert "reported a failure" in notifications[-1]["message"]
    assert (
        "fresh steam.inf verification passed"
        in (notifications[-1]["details"]["SteamCMD Reconciliation"])
    )
    assert "recovery start succeeded" in notifications[-1]["details"]["Operation Result"]
    assert state.log.status == "success"
    assert state.server_updates == 1


@pytest.mark.asyncio
async def test_steamcmd_reported_failure_stays_failed_when_delayed_version_is_stale(monkeypatch):
    state = FakeDatabaseState()

    sleep_calls = []

    async def capture_sleep(delay):
        sleep_calls.append(delay)

    refresh_calls = 0

    async def refresh_version(server):
        nonlocal refresh_calls
        refresh_calls += 1
        return True, "1.41.6.9"

    steam_check_calls = 0

    async def final_steam_check(version):
        nonlocal steam_check_calls
        steam_check_calls += 1
        return True, {"up_to_date": False, "required_version": "1.41.7.0"}

    notifications = install_trigger_fakes(
        monkeypatch,
        state,
        SteamCMDReportedFailureSSHManager,
        refresh_version,
        final_steam_check,
        capture_sleep,
    )

    service = AutoUpdateService()
    await service._trigger_server_update(
        make_server(),
        current_version="1.41.6.9",
        required_version="1.41.7.0",
        version_source="steam.inf",
    )

    assert len(sleep_calls) == 10
    assert sum(sleep_calls) == pytest.approx(300)
    assert refresh_calls == len(sleep_calls) + 1
    assert steam_check_calls == 1
    assert [item["title"] for item in notifications] == [
        "Automatic update started",
        "Automatic update failed",
    ]
    assert notifications[-1]["details"]["Observed Version"] == "1.41.6.9"
    assert state.log.status == "failed"
    assert state.server_updates == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manager_class",
    [NonSteamCMDFailureSSHManager, SteamCMDRecoveryFailureSSHManager],
)
async def test_non_reconcilable_failure_is_reported_immediately(monkeypatch, manager_class):
    state = FakeDatabaseState()

    sleep_calls = []

    async def capture_sleep(delay):
        sleep_calls.append(delay)

    refresh_calls = 0

    async def matching_refresh(server):
        nonlocal refresh_calls
        refresh_calls += 1
        return True, "1.41.7.0"

    steam_check_calls = 0

    async def matching_steam_check(version):
        nonlocal steam_check_calls
        steam_check_calls += 1
        return True, {"up_to_date": True, "required_version": "1.41.7.0"}

    notifications = install_trigger_fakes(
        monkeypatch,
        state,
        manager_class,
        matching_refresh,
        matching_steam_check,
        capture_sleep,
    )

    service = AutoUpdateService()
    await service._trigger_server_update(
        make_server(),
        current_version="1.41.6.9",
        required_version="1.41.7.0",
        version_source="steam.inf",
    )

    assert sleep_calls == []
    assert refresh_calls == 0
    assert steam_check_calls == 0
    assert [item["title"] for item in notifications] == [
        "Automatic update started",
        "Automatic update failed",
    ]
    assert state.log.status == "failed"
    assert state.server_updates == 0


@pytest.mark.asyncio
async def test_learned_numeric_required_version_matches_later_dotted_version(monkeypatch):
    observed_versions = iter(("1.41.6.9", "1.41.7.0"))

    async def ignore_progress(message):
        return None

    async def refresh_version(server):
        return True, next(observed_versions)

    steam_check_calls = 0

    async def learn_required_version(version):
        nonlocal steam_check_calls
        steam_check_calls += 1
        return True, {"up_to_date": False, "required_version": "14170"}

    sleep_calls = []

    async def capture_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(
        "services.auto_update_service.steam_inf_service.refresh_version_cache",
        refresh_version,
    )
    monkeypatch.setattr(
        "services.auto_update_service.steam_api_service.check_version",
        learn_required_version,
    )
    monkeypatch.setattr("services.auto_update_service.asyncio.sleep", capture_sleep)

    service = AutoUpdateService()
    verified, observed, latest_required = await service._wait_for_updated_version(
        make_server(),
        required_version=None,
        log_progress=ignore_progress,
    )

    assert verified is True
    assert observed == "1.41.7.0"
    assert latest_required == "14170"
    assert steam_check_calls == 1
    assert sleep_calls == [30]
