"""覆盖 GitHub 插件安装的计划校验、重试和隔离式远端流程。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules import GitHubPluginInstallRequest, GitHubPluginInstallResponse
from services import plugin_diagnostic_service as diagnostics
from services import plugin_installation as installation


def _server(**overrides):
    values = dict(
        id=3,
        user_id=7,
        game_directory="/srv/cs2",
        use_panel_proxy=False,
        github_proxy="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(**overrides):
    values = dict(
        download_url="https://github.com/acme/plugin/releases/download/v1/plugin.zip",
        exclude_dirs=[],
        exclude_files=[],
        record_installation=False,
        suppress_notification=True,
    )
    values.update(overrides)
    return GitHubPluginInstallRequest(**values)


def test_installation_command_builders_and_retryability():
    token = installation._operation_token("  run/with unsafe chars  ")
    assert token == "run-with-unsafe-chars"
    assert installation._operation_token(None)
    assert installation._remote_plugin_temp_dir(3, "op") == "/tmp/upkk-plugin-3-op"
    rsync = installation._build_plugin_copy_command(
        "/source dir", "/target", ["cfg/*.json"], use_rsync=True
    )
    tar = installation._build_plugin_copy_command("/source", "/target", ["cfg"], use_rsync=False)
    plain = installation._build_plugin_copy_command("/source", "/target", [], use_rsync=False)
    assert "rsync" in rsync and "--exclude" in rsync and "gamedata" in rsync
    assert "tar" in tar and "gamedata" in tar
    assert plain.startswith("cp -r")
    assert "manifest.tsv" in installation._build_backup_command("/a", "/b", "/c")
    assert "manifest.tsv" in installation._build_rollback_command("/b", "/c")
    assert installation._is_retryable_install_failure("network timeout")
    assert not installation._is_retryable_install_failure("server not found")
    assert not installation._is_retryable_install_failure("invalid custom install path")


@pytest.mark.asyncio
async def test_get_server_for_user_admin_and_owner(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    admin_server = _server()
    owner_server = _server(id=4)
    monkeypatch.setattr(installation.Server, "get_by_id", AsyncMock(return_value=admin_server))
    monkeypatch.setattr(
        installation.Server, "get_by_id_and_user", AsyncMock(return_value=owner_server)
    )
    assert (
        await installation.get_server_for_user(db, 3, SimpleNamespace(is_admin=True))
        is admin_server
    )
    assert (
        await installation.get_server_for_user(db, 4, SimpleNamespace(is_admin=False, id=7))
        is owner_server
    )
    with pytest.raises(LookupError, match="Server not found"):
        monkeypatch.setattr(installation.Server, "get_by_id", AsyncMock(return_value=None))
        await installation.get_server_for_user(db, 9, SimpleNamespace(is_admin=True))
    assert db.commit.await_count == 2


class _InstallSSH:
    def __init__(self, **_kwargs):
        self.commands = []
        self.count_queries = 0

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        return None

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        if command.startswith("test -d /srv/cs2/cs2/game/csgo"):
            return True, "exists", ""
        if command.startswith("curl "):
            return True, "downloaded", ""
        if command.startswith("stat "):
            return True, "2048", ""
        if "find /tmp/" in command and "-name 'addons'" in command:
            return True, "", ""
        if "addons_found" in command:
            return True, "addons_found", ""
        if "wc -l" in command:
            self.count_queries += 1
            return True, "2" if self.count_queries == 1 else "5", ""
        if command == "command -v rsync":
            return True, "/usr/bin/rsync", ""
        return True, "", ""

    async def upload_file_with_progress(self, *_args, **_kwargs):
        return True, ""


@pytest.mark.asyncio
async def test_install_github_plugin_remote_success(monkeypatch):
    server = _server(github_proxy="https://proxy.example")
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7, is_admin=True)
    monkeypatch.setattr(installation, "SSHManager", _InstallSSH)
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())
    result = await installation.install_github_plugin(
        3,
        _request(exclude_dirs=["cfg"], exclude_files=["addons/plugin.cfg"]),
        db,
        user,
        operation_id="safe-op",
    )
    assert result.success and result.installed_files == 3


@pytest.mark.asyncio
async def test_install_github_plugin_early_failures_and_retry(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7, is_admin=False)
    server = _server()
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))

    class _NoConnect(_InstallSSH):
        async def connect(self, _server):
            return False, "offline"

    monkeypatch.setattr(installation, "SSHManager", _NoConnect)
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "SSH connection failed" in result.message

    class _NoCs2(_InstallSSH):
        async def execute_command(self, command, **kwargs):
            self.commands.append(command)
            if command.startswith("test -d /srv/cs2/cs2/game/csgo"):
                return False, "", ""
            return await super().execute_command(command, **kwargs)

    monkeypatch.setattr(installation, "SSHManager", _NoCs2)
    result = await installation.install_github_plugin(3, _request(), db, user)
    assert not result.success and "CS2 server not found" in result.message

    outcomes = iter(
        [
            GitHubPluginInstallResponse(success=False, message="network timeout"),
            GitHubPluginInstallResponse(success=True, message="ok", installed_files=2),
        ]
    )
    monkeypatch.setattr(
        installation, "install_github_plugin", AsyncMock(side_effect=lambda *a, **k: next(outcomes))
    )
    progress = AsyncMock()
    result = await installation.install_github_plugin_with_retry(
        3, _request(), db, user, ai_progress=progress, max_retries=2
    )
    assert result.success and progress.await_count >= 2

    monkeypatch.setattr(
        installation,
        "install_github_plugin",
        AsyncMock(
            return_value=GitHubPluginInstallResponse(success=False, message="server not found")
        ),
    )
    result = await installation.install_github_plugin_with_retry(
        3, _request(), db, user, max_retries=3
    )
    assert not result.success and "after" not in result.message


@pytest.mark.asyncio
async def test_installation_archive_formats_custom_path_and_tracking(monkeypatch):
    server = _server()
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())
    monkeypatch.setattr(installation, "SSHManager", _InstallSSH)
    for suffix in (".tar.gz", ".tgz", ".tar", ".7z", ".bin"):
        result = await installation.install_github_plugin(
            3,
            _request(
                download_url=f"https://github.com/acme/plugin/releases/download/v1/plugin{suffix}"
            ),
            db,
            user,
        )
        assert result.success is True

    class _CustomPathSSH(_InstallSSH):
        async def execute_command(self, command, **kwargs):
            if "echo 'addons_found'" in command:
                return True, "", ""
            if command.startswith("find ") and "-name 'addons'" in command:
                return True, "", ""
            if "wc -l" in command:
                return True, "6", ""
            return await super().execute_command(command, **kwargs)

    monkeypatch.setattr(installation, "SSHManager", _CustomPathSSH)
    monkeypatch.setattr("services.plugins.tracking.upsert_managed_plugin", AsyncMock())
    monkeypatch.setattr(
        "services.plugins.tracking.canonical_repo_url", lambda value: value.rstrip("/")
    )
    monkeypatch.setattr("services.plugins.tracking.derive_asset_glob", lambda *_a: "*.zip")
    result = await installation.install_github_plugin(
        3,
        _request(
            custom_install_path="addons/custom",
            exclude_files=["cfg/plugin.cfg"],
            exclude_dirs=["cfg"],
            record_installation=True,
            repo_url="https://github.com/acme/plugin/",
            display_name="Plugin",
            release_id="r1",
            release_tag="v1",
            asset_name="plugin.zip",
        ),
        db,
        user,
    )
    assert result.success and result.installed_files == 6


@pytest.mark.asyncio
async def test_installation_secure_gateway_digest_and_upload_failures(monkeypatch, tmp_path: Path):
    server = _server()
    db = SimpleNamespace(commit=AsyncMock())
    user = SimpleNamespace(id=7, is_admin=False)
    archive = tmp_path / "plugin.zip"
    archive.write_bytes(b"archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(installation, "get_server_for_user", AsyncMock(return_value=server))
    monkeypatch.setattr(installation, "send_deployment_update", AsyncMock())

    class _SecureSSH(_InstallSSH):
        async def upload_file_with_progress(self, *_args, **_kwargs):
            return True, ""

        async def execute_command(self, command, **kwargs):
            if "sha256sum" in command:
                return True, digest, ""
            return await super().execute_command(command, **kwargs)

    monkeypatch.setattr(installation, "SSHManager", _SecureSSH)
    monkeypatch.setattr(
        "services.plugins.github_assets.download_release_asset",
        AsyncMock(return_value=(str(archive), digest, archive.stat().st_size)),
    )
    result = await installation.install_github_plugin(
        3,
        _request(expected_archive_sha256=digest),
        db,
        user,
    )
    assert result.success

    archive.write_bytes(b"archive")
    monkeypatch.setattr(
        "services.plugins.github_assets.download_release_asset",
        AsyncMock(return_value=(str(archive), "0" * 64, archive.stat().st_size)),
    )
    result = await installation.install_github_plugin(
        3, _request(expected_archive_sha256=digest), db, user
    )
    assert "digest changed" in result.message

    archive.write_bytes(b"archive")

    class _UploadFailure(_SecureSSH):
        async def upload_file_with_progress(self, *_args, **_kwargs):
            return False, "disk full"

    monkeypatch.setattr(installation, "SSHManager", _UploadFailure)
    monkeypatch.setattr(
        "services.plugins.github_assets.download_release_asset",
        AsyncMock(return_value=(str(archive), digest, archive.stat().st_size)),
    )
    result = await installation.install_github_plugin(
        3, _request(expected_archive_sha256=digest), db, user
    )
    assert "upload" in result.message


class _DbResult:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.scalar


class _DiagnosticDb:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return _DbResult(self.rows)

    async def get(self, *_args):
        return SimpleNamespace(id=7)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_diagnostic_inventory_alias_groups_and_payload(monkeypatch):
    assert diagnostics._plugin_alias("Counter-Strike_ShArp.dll") == "counterstrikesharpdll"
    managed = [
        SimpleNamespace(
            id=1,
            market_plugin_id=10,
            display_name="Alpha",
            repo_url="https://x/alpha",
            custom_install_path=None,
        ),
        SimpleNamespace(
            id=None,
            market_plugin_id=11,
            display_name="unused",
            repo_url=None,
            custom_install_path=None,
        ),
    ]
    aliases = {"counterstrikesharp:Alpha": {"alpha"}}
    assert diagnostics._link_managed_plugins(managed, aliases) == {10: "counterstrikesharp:Alpha"}
    candidates = [
        {
            "key": "counterstrikesharp:Alpha",
            "kind": "counterstrikesharp",
            "name": "Alpha",
            "relative_path": "x",
            "revision": "a" * 64,
        },
        {
            "key": "counterstrikesharp:Beta",
            "kind": "counterstrikesharp",
            "name": "Beta",
            "relative_path": "y",
            "revision": "b" * 64,
        },
    ]
    db = _DiagnosticDb(managed)
    monkeypatch.setattr(diagnostics.MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    groups = await diagnostics._group_candidates(db, 3, candidates)
    assert len(groups) == 2 and all(item["reason"] == "independent" for item in groups)
    assert diagnostics._expand_groups({"g": ["a", "b"]}, ["g"]) == ["a", "b"]
    assert diagnostics._health_policy(SimpleNamespace(enable_a2s_monitoring=True))["a2s_required"]
    assert len(diagnostics._plan_hash({"a": 1})) == 64

    class _InventorySSH:
        async def connect(self, _server):
            return True, ""

        async def execute_command(self, command, **_kwargs):
            if "find" in command and "-printf" in command:
                return (
                    True,
                    "metamod\t/srv/cs2/cs2/game/csgo/addons/metamod/alpha.vdf\t1:2\n"
                    "counterstrikesharp\t/srv/cs2/cs2/game/csgo/addons/counterstrikesharp/plugins/Beta\t1:2\n",
                    "",
                )
            return True, "a" * 64, ""

        async def disconnect(self):
            return None

    monkeypatch.setattr(diagnostics, "SSHManager", _InventorySSH)
    server = SimpleNamespace(game_directory="/srv/cs2")
    inventory = await diagnostics._inventory(server)
    assert [item["name"] for item in inventory] == ["Beta", "alpha.vdf"]

    plan_server = SimpleNamespace(id=3, user_id=7, enable_a2s_monitoring=False)
    monkeypatch.setattr(diagnostics, "authorized_server", AsyncMock(return_value=plan_server))
    monkeypatch.setattr(diagnostics, "_inventory", AsyncMock(return_value=[]))
    monkeypatch.setattr(diagnostics, "_group_candidates", AsyncMock(return_value=[]))
    plan = await diagnostics.build_diagnostic_plan(db, SimpleNamespace(), 3, "both")
    assert plan["warnings"] and plan["estimated_max_starts"] >= 2

    entry_manager = SimpleNamespace(
        validate_path_within_base=AsyncMock(return_value=(True, "")),
        execute_command=AsyncMock(return_value=(True, "", "")),
    )
    entry = SimpleNamespace(
        candidate_key="counterstrikesharp:alpha",
        source_relative_path="cs2/game/csgo/addons/alpha",
        quarantine_relative_path=".upkk/quarantine/alpha",
        is_quarantined=False,
        restored_at=None,
    )
    await diagnostics._move_entry(entry_manager, server, entry, quarantine=True)
    assert entry.is_quarantined
    await diagnostics._move_entry(entry_manager, server, entry, quarantine=False)
    assert not entry.is_quarantined
    await diagnostics._validate_remote_path(entry_manager, server, "/srv/cs2/x", allow_missing=True)
    entry_manager.validate_path_within_base.return_value = (False, "outside")
    with pytest.raises(ValueError, match="outside"):
        await diagnostics._validate_remote_path(
            entry_manager, server, "/etc/passwd", allow_missing=False
        )


@pytest.mark.asyncio
async def test_diagnostic_health_attempt_and_payload(monkeypatch):
    run = SimpleNamespace(
        id="run", requested_by=7, start_attempts=0, health_policy={"a2s_required": False}
    )
    server = SimpleNamespace(id=3, game_directory="/srv/cs2", host="h", game_port=27015)
    db = _DiagnosticDb()
    monkeypatch.setattr(diagnostics, "authorized_server", AsyncMock(return_value=server))
    manager = SimpleNamespace(
        start_server=AsyncMock(return_value=(True, "started")),
        get_server_status=AsyncMock(return_value=(True, "running")),
        execute_command=AsyncMock(side_effect=[(True, "0", ""), (True, "", "")]),
    )
    monkeypatch.setattr(diagnostics.asyncio, "sleep", AsyncMock())
    assert await diagnostics._health_attempt(db, run, server, manager, "phase", ["x"], None)
    assert run.start_attempts == 1 and db.added
    monkeypatch.setattr(diagnostics, "_has_diagnostic_blocker", AsyncMock(return_value=False))
    assert not await diagnostics.has_diagnostic_blocker(3, db)
    progress = AsyncMock()
    await diagnostics._emit_readable_progress(progress, "unknown", ["x"])
    assert progress.await_count == 1

    step = SimpleNamespace(
        sequence=1, phase="phase", candidate_keys=["x"], healthy=True, evidence={}
    )
    quarantine = SimpleNamespace(
        candidate_key="x", source_relative_path="a", is_quarantined=True, is_culprit=False
    )

    class _PayloadDb(_DiagnosticDb):
        def __init__(self):
            super().__init__()
            self.responses = iter([_DbResult([step]), _DbResult([quarantine])])

        async def execute(self, _statement):
            return next(self.responses)

    payload_db = _PayloadDb()
    payload_run = SimpleNamespace(
        id="run",
        server_id=3,
        requested_by=7,
        scope="both",
        status="completed",
        plan_hash="h",
        culprit_keys=None,
        start_attempts=1,
        error=None,
        created_at=None,
        completed_at=None,
    )
    payload = await diagnostics.diagnostic_run_payload(payload_db, payload_run)
    assert payload["culprit_keys"] == [] and payload["steps"][0]["phase"] == "phase"


@pytest.mark.asyncio
async def test_diagnostic_recommendation_latest_restore_and_restart(monkeypatch):
    server = SimpleNamespace(
        id=3,
        user_id=7,
        last_update_time=None,
        enable_a2s_monitoring=False,
        host="h",
        game_port=27015,
        game_directory="/srv/cs2",
    )
    monkeypatch.setattr(diagnostics, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        "services.server_monitor.server_monitor.get_restart_info",
        lambda _id: {"restart_count": 0, "can_restart": True, "max_restarts": 3},
    )
    recommendation = await diagnostics.get_diagnostic_recommendation(
        SimpleNamespace(), SimpleNamespace(), 3
    )
    assert recommendation["recommended"] is False
    monkeypatch.setattr(
        "services.server_monitor.server_monitor.get_restart_info",
        lambda _id: {"restart_count": 3, "can_restart": False, "max_restarts": 3},
    )
    recommendation = await diagnostics.get_diagnostic_recommendation(
        SimpleNamespace(), SimpleNamespace(), 3
    )
    assert recommendation["reason"] == "restart_loop_protection"

    entry = SimpleNamespace(
        candidate_key="x",
        source_relative_path="a",
        quarantine_relative_path=".upkk/q/a",
        is_quarantined=True,
        is_culprit=True,
        restored_at=None,
    )
    run = SimpleNamespace(
        id="d" * 36,
        server_id=3,
        requested_by=7,
        scope="both",
        status="running",
        plan_hash="h",
        culprit_keys=["x"],
        start_attempts=1,
        error=None,
        created_at=None,
        completed_at=None,
        original_server_running=True,
    )

    class _RestoreDb(_DiagnosticDb):
        def __init__(self):
            super().__init__()
            self.responses = iter(
                [
                    _DbResult(scalar=run),
                    _DbResult([entry]),
                    _DbResult([]),
                    _DbResult([]),
                ]
            )

        async def execute(self, _statement):
            return next(self.responses)

        async def refresh(self, _item):
            return None

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _RestoreSSH:
        async def connect(self, _server):
            return True, ""

        async def disconnect(self):
            return None

        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server):
            return True, "started"

        async def validate_path_within_base(self, *_args, **_kwargs):
            return True, ""

        async def execute_command(self, _command, **_kwargs):
            return True, "", ""

    monkeypatch.setattr(diagnostics.maintenance_lock_service, "get", lambda *_a, **_k: _Lock())
    monkeypatch.setattr(diagnostics, "SSHManager", _RestoreSSH)
    monkeypatch.setattr(diagnostics, "_blocked_servers", {3})
    result = await diagnostics.restore_diagnostic_run(
        _RestoreDb(), SimpleNamespace(id=7), 3, run.id
    )
    assert result["status"] == "restored"
    assert entry.is_quarantined is False and run.culprit_keys == []

    monkeypatch.setattr(diagnostics, "authorized_server", AsyncMock(return_value=server))
    with pytest.raises(LookupError):
        await diagnostics.get_latest_diagnostic_run(_DiagnosticDb([]), SimpleNamespace(id=7), 3)


@pytest.mark.asyncio
async def test_execute_diagnostic_plan_rejects_stale_and_empty_plans(monkeypatch):
    server = SimpleNamespace(id=3, user_id=7)
    monkeypatch.setattr(diagnostics.maintenance_lock_service, "get", lambda *_a, **_k: _NoopLock())
    monkeypatch.setattr(diagnostics, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        diagnostics,
        "build_diagnostic_plan",
        AsyncMock(return_value={"plan_hash": "good", "candidates": []}),
    )
    with pytest.raises(ValueError, match="changed"):
        await diagnostics.execute_diagnostic_plan(
            _DiagnosticDb(), SimpleNamespace(id=7), 3, "both", "bad"
        )
    with pytest.raises(ValueError, match="No plugin"):
        await diagnostics.execute_diagnostic_plan(
            _DiagnosticDb(), SimpleNamespace(id=7), 3, "both", "good"
        )


class _NoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None
