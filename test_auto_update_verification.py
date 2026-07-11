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


def make_server():
    return Server(
        id=77, user_id=1, name="Verifier", host="127.0.0.1",
        ssh_user="steam", auth_type=AuthType.PASSWORD,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observed,steam_up_to_date,expected_success",
    [("1.41.6.9", False, True), ("1.41.6.8", False, False), ("1.41.7.0", True, True)],
)
async def test_terminal_notification_depends_on_steam_inf(monkeypatch, observed, steam_up_to_date, expected_success):
    state = FakeDatabaseState()
    monkeypatch.setattr("modules.database.async_session_maker", lambda: FakeSession(state))
    monkeypatch.setattr("services.auto_update_service.SSHManager", SuccessfulSSHManager)

    async def refresh_version(server):
        return True, observed

    async def final_steam_check(version):
        return True, {"up_to_date": steam_up_to_date, "required_version": "1.41.7.0" if steam_up_to_date else "1.41.6.9"}

    monkeypatch.setattr("services.auto_update_service.steam_inf_service.refresh_version_cache", refresh_version)
    monkeypatch.setattr("services.auto_update_service.steam_api_service.check_version", final_steam_check)
    notifications = []

    def capture(*args, **kwargs):
        notifications.append({"title": kwargs.get("title"), "details": kwargs.get("details"), "state": kwargs.get("state")})
        return True

    monkeypatch.setattr("services.auto_update_service.discord_notification_service.queue_notify", capture)
    service = AutoUpdateService()
    await service._trigger_server_update(
        make_server(), current_version="1.41.6.8",
        required_version="1.41.6.9", version_source="steam.inf",
    )

    assert notifications[0]["title"] == "Automatic update started"
    assert notifications[0]["state"] == "in_progress"
    assert notifications[-1]["details"]["Observed Version"] == observed
    assert (notifications[-1]["title"] == "Automatic update completed") is expected_success
    assert state.log.status == ("success" if expected_success else "failed")
