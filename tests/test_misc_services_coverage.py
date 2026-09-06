"""覆盖低依赖基础服务的成功、失败和取消分支。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules import db_admin
from services.a2s_query import A2SQueryService
from services.disk_space_service import DiskSpaceService
from services.email_service import EmailService
from services.plugins import github_assets
from services.ssh_health_monitor import SSHHealthMonitor


@pytest.mark.asyncio
async def test_db_admin_run_and_main(monkeypatch, capsys):
    status = SimpleNamespace(
        server_version_num=160000,
        current_heads=["a"],
        code_heads=["a"],
        is_current=True,
    )
    dispose = AsyncMock()
    monkeypatch.setattr(db_admin, "upgrade_database", AsyncMock(return_value=status))
    monkeypatch.setattr(db_admin, "database_status", AsyncMock(return_value=status))
    monkeypatch.setattr(db_admin, "engine", SimpleNamespace(dispose=dispose))
    assert await db_admin._run("upgrade") == 0
    assert await db_admin._run("status") == 0
    assert '"is_current": true' in capsys.readouterr().out
    monkeypatch.setattr(
        db_admin,
        "database_status",
        AsyncMock(side_effect=db_admin.DatabaseMigrationError("bad")),
    )
    assert await db_admin._run("check") == 1
    assert "database migration error" in capsys.readouterr().out
    monkeypatch.setattr(db_admin, "_run", lambda _command: 0)
    monkeypatch.setattr(db_admin.asyncio, "run", lambda _coro: 0)
    monkeypatch.setattr(
        db_admin.argparse.ArgumentParser,
        "parse_args",
        lambda _self: SimpleNamespace(command="status"),
    )
    assert db_admin.main() == 0


@pytest.mark.asyncio
async def test_disk_space_cache_read_and_parse(monkeypatch):
    service = DiskSpaceService()
    server = SimpleNamespace(id=4, host="host", ssh_port=22, game_directory="/srv/cs2")
    redis = SimpleNamespace(
        get=AsyncMock(return_value={"used_gb": 1}), set=AsyncMock(), delete=AsyncMock()
    )
    monkeypatch.setattr("services.disk_space_service.redis_manager", redis)
    assert await service.get_disk_space(server) == (True, {"used_gb": 1})
    redis.get.return_value = None
    assert await service.get_disk_space(server, cache_only=True) == (False, None)
    monkeypatch.setattr(
        service, "_read_disk_space", AsyncMock(return_value=(True, {"total_gb": 2}))
    )
    assert await service.get_disk_space(server, force_refresh=True) == (True, {"total_gb": 2})
    assert service._parse_df_output("dev 10G 2G 8G 20% /", 2) == {
        "used_gb": 2.0,
        "total_gb": 10.0,
        "available_gb": 8.0,
        "used_percent": 20.0,
    }
    assert service._parse_df_output("bad", 1) is None
    assert service._parse_df_output("dev xG 2G yG 20% /", 1) is None

    class _SSH:
        async def connect(self, _server):
            return True, ""

        def __init__(self, values):
            self.values = iter(values)

        async def execute_command(self, _command, **_kwargs):
            return next(self.values)

        async def disconnect(self):
            return None

    service._read_disk_space = DiskSpaceService._read_disk_space.__get__(service)
    monkeypatch.setattr(
        "services.disk_space_service.SSHManager", lambda: _SSH([(False, "", "bad")])
    )
    assert await service._read_disk_space(server) == (False, None)
    monkeypatch.setattr(
        "services.disk_space_service.SSHManager",
        lambda: _SSH([(True, "1073741824", ""), (True, "Filesystem 10G 2G 8G 20% /", "")]),
    )
    assert (await service._read_disk_space(server))[0] is True
    await service.clear_disk_space_cache(4)


@pytest.mark.asyncio
async def test_a2s_success_empty_timeout_and_errors(monkeypatch):
    info = SimpleNamespace(
        server_name="srv",
        map_name="de_dust2",
        folder="csgo",
        game="CS2",
        player_count=1,
        max_players=10,
        bot_count=0,
        server_type="d",
        platform="l",
        password_protected=False,
        vac_enabled=True,
        version="1",
        ping=0.1,
        keywords="x",
        game_id=730,
    )
    monkeypatch.setattr("services.a2s_query.asyncio.to_thread", AsyncMock(return_value=info))
    ok, payload = await A2SQueryService.query_server_info("host", 27015)
    assert ok and payload["server_name"] == "srv" and payload["game_id"] == 730
    monkeypatch.setattr("services.a2s_query.asyncio.to_thread", AsyncMock(return_value=None))
    assert await A2SQueryService.query_server_info("h", 1) == (False, None)
    monkeypatch.setattr("services.a2s_query.asyncio.to_thread", AsyncMock(return_value=[]))
    assert await A2SQueryService.query_players("h", 1) == (True, [])
    assert await A2SQueryService.query_rules("h", 1) == (True, {})
    monkeypatch.setattr(
        "services.a2s_query.asyncio.to_thread", AsyncMock(side_effect=asyncio.TimeoutError())
    )
    assert await A2SQueryService.query_players("h", 1) == (False, None)
    assert await A2SQueryService.query_rules("h", 1) == (False, None)
    monkeypatch.setattr(
        "services.a2s_query.asyncio.to_thread", AsyncMock(side_effect=RuntimeError("bad"))
    )
    assert await A2SQueryService.check_server_health("h", 1) is False


def test_github_asset_url_validation():
    github_assets.validate_download_url("https://github.com/acme/repo/releases/download/v1/a.zip")
    for url in (
        "http://github.com/acme/repo/releases/download/v1/a.zip",
        "https://evil.example/a.zip",
        "https://github.com:443/acme/repo/releases/download/v1/a.zip",
        "https://github.com/acme/repo/releases/download/v1/a.zip?x=1",
        "https://u:p@github.com/acme/repo/releases/download/v1/a.zip",
    ):
        with pytest.raises(github_assets.GitHubPlanError):
            github_assets.validate_download_url(url)


class _StreamResponse:
    def __init__(self, status=200, chunks=(b"abc",), location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_bytes(self, _size):
        for chunk in self._chunks:
            yield chunk


class _HttpClient:
    def __init__(self, responses):
        self.responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *_args, **_kwargs):
        return next(self.responses)


@pytest.mark.asyncio
async def test_github_asset_download_redirect_and_failures(monkeypatch):
    monkeypatch.setattr(
        github_assets.httpx,
        "AsyncClient",
        lambda **_kwargs: _HttpClient(
            [
                _StreamResponse(302, location="https://objects.githubusercontent.com/a.zip"),
                _StreamResponse(200, (b"a", b"b")),
            ]
        ),
    )
    path, digest, size = await github_assets.download_release_asset(
        "https://github.com/a/b/releases/download/v1/a.zip"
    )
    assert size == 2 and len(digest) == 64
    monkeypatch.setattr(
        github_assets.httpx, "AsyncClient", lambda **_kwargs: _HttpClient([_StreamResponse(500)])
    )
    with pytest.raises(github_assets.GitHubPlanError, match="HTTP 500"):
        await github_assets.download_release_asset(
            "https://github.com/a/b/releases/download/v1/a.zip"
        )


@pytest.mark.asyncio
async def test_ssh_health_due_auth_and_lifecycle(monkeypatch):
    monitor = SSHHealthMonitor()
    now = datetime.now(timezone.utc)
    server = SimpleNamespace(
        id=1,
        ssh_health_check_interval_hours=2,
        last_ssh_health_check=None,
        ssh_health_status="healthy",
        is_password_auth=True,
        is_key_auth=False,
        host="h",
        ssh_port=22,
        ssh_user="u",
        ssh_password="p",
        ssh_key_path="k",
    )
    assert monitor._check_due(server, now)
    monitor.last_check_times[1] = now
    assert not monitor._check_due(server, now)
    server.ssh_health_status = "completely_down"
    monitor.last_check_times.clear()
    assert not monitor._check_due(server, now)

    conn = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(
        "services.ssh_health_monitor.asyncssh.connect", AsyncMock(return_value=conn)
    )
    assert await monitor._test_ssh_connection(server)
    server.is_password_auth = False
    server.is_key_auth = True
    assert await monitor._test_ssh_connection(server)
    server.is_key_auth = False
    assert not await monitor._test_ssh_connection(server)
    monkeypatch.setattr(
        "services.ssh_health_monitor.asyncssh.connect",
        AsyncMock(side_effect=asyncio.TimeoutError()),
    )
    server.is_password_auth = True
    assert not await monitor._test_ssh_connection(server)
    monkeypatch.setattr(
        "services.ssh_health_monitor.asyncssh.connect", AsyncMock(side_effect=PermissionError())
    )
    assert not await monitor._test_ssh_connection(server)

    monitor.running = True
    monitor.monitor_task = asyncio.create_task(asyncio.sleep(10))
    await monitor.stop()
    await monitor.start()
    await monitor.stop()


def _email_settings(**overrides):
    values = dict(
        email_enabled=True,
        email_provider="smtp",
        smtp_host="smtp.example",
        smtp_port=25,
        smtp_username="sender",
        smtp_password="secret",
        smtp_use_tls=True,
        email_from_address="from@example.com",
        gmail_token_json=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_email_provider_selection_smtp_and_template(monkeypatch):
    service = EmailService()
    db = SimpleNamespace(commit=AsyncMock())
    settings = _email_settings()
    monkeypatch.setattr(
        "services.email_service.SystemSettings.get_or_create_settings",
        AsyncMock(return_value=settings),
    )

    class _SMTP:
        def starttls(self):
            self.tls = True

        def login(self, *_args):
            return None

        def sendmail(self, *_args):
            return None

        def quit(self):
            return None

    monkeypatch.setattr("services.email_service.smtplib.SMTP", lambda *args, **kwargs: _SMTP())
    assert await service.send_email(db, "to@example.com", "subject", "<p>html</p>", "text")
    settings.email_provider = "unknown"
    assert not await service.send_email(db, "to", "s", "h")
    settings.email_enabled = False
    assert not await service.send_email(db, "to", "s", "h")
    assert not service._send_via_smtp_sync(_email_settings(smtp_host=""), "to", "s", "h")
    monkeypatch.setattr("services.email_service.smtplib.SMTP_SSL", lambda *args, **kwargs: _SMTP())
    assert service._send_via_smtp_sync(_email_settings(smtp_use_tls=False), "to", "s", "h")
    settings.email_enabled = True
    settings.email_provider = "gmail"
    settings.gmail_token_json = None
    assert not await service.send_email(db, "to", "s", "h")
    settings.gmail_token_json = "not-json"
    assert not service._send_via_gmail_api_sync(settings, "to", "s", "h")
    html, text = service.get_password_reset_template("https://example/reset", "alice")
    assert "alice" in html and "https://example/reset" in text
