"""覆盖旧版 GitHub 插件路由的鉴权、解析、归档分析和文件操作分支。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.routes import github_plugins as routes
from modules import (
    GitHubInstallRecipeCreate,
    GitHubPluginInspectRequest,
    GitHubPluginInstallExecuteRequest,
    GitHubPluginInstallPlanRequest,
    GitHubPluginInstallRequest,
    PluginUninstallRequest,
)
from services.ai_access import AgentAccessDenied
from services.github_plugin_plan_service import GitHubPlanError


def _user(**overrides):
    values = {"id": 7, "is_admin": False, "is_active": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _server(**overrides):
    values = {
        "id": 3,
        "user_id": 7,
        "game_directory": "/srv/cs2",
        "github_proxy": None,
        "use_panel_proxy": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _db():
    return SimpleNamespace(commit=AsyncMock())


def _install_request(**overrides):
    values = {
        "download_url": "https://github.com/acme/demo/releases/download/v1/demo.zip",
        "repo_url": "https://github.com/acme/demo",
        "installation_plan_hash": "a" * 64,
        "asset_name": "demo.zip",
    }
    values.update(overrides)
    return GitHubPluginInstallRequest(**values)


def test_url_copy_helpers_and_safe_errors():
    assert routes.parse_github_url("https://github.com/acme/demo/issues") == ("acme", "demo")
    with pytest.raises(ValueError):
        routes.parse_github_url("https://gitlab.com/acme/demo")
    assert "rsync" in routes._build_plugin_copy_command("/a b", "/c", ["cfg"], use_rsync=True)
    assert "tar" in routes._build_plugin_copy_command("/a", "/c", ["cfg"], use_rsync=False)
    assert routes._build_plugin_copy_command("/a", "/c", [], use_rsync=False).startswith("cp -r")
    assert routes._safe_github_error(AgentAccessDenied("no")).status_code == 404
    assert routes._safe_github_error(PermissionError("forbidden")).status_code == 403
    assert routes._safe_github_error(GitHubPlanError("conflict")).status_code == 409


@pytest.mark.asyncio
async def test_secure_archive_analysis_success_and_failures(monkeypatch, tmp_path: Path):
    db = _db()
    user = _user()
    authorize = AsyncMock(return_value=_server())
    monkeypatch.setattr("services.ai_access.authorized_server", authorize)
    monkeypatch.setattr(routes, "enforce_agent_rate_limit", AsyncMock())
    archive = tmp_path / "demo.zip"
    archive.write_bytes(b"archive")
    entries = [
        {"path": "addons", "is_dir": True, "size": 0},
        {"path": "addons/demo.dll", "is_dir": False, "size": 12},
        {"path": "cfg/demo.cfg", "is_dir": False, "size": 4},
    ]
    monkeypatch.setattr(routes, "_validate_download_url", Mock())
    monkeypatch.setattr(
        routes, "_download_release_asset", AsyncMock(return_value=(str(archive), "d", 10))
    )
    monkeypatch.setattr(routes, "_archive_entries", Mock(return_value=entries))
    response = await routes._secure_archive_analysis(
        db, user, 3, "https://github.com/a/b/releases/download/v1/demo.zip"
    )
    assert response.success and response.has_addons_dir and response.archive_type == "zip"
    assert (
        response.total_size if hasattr(response, "total_size") else response.all_files[0].size == 12
    )
    assert not archive.exists()

    monkeypatch.setattr(
        routes, "_download_release_asset", AsyncMock(side_effect=GitHubPlanError("bad archive"))
    )
    failed = await routes._secure_archive_analysis(
        db, user, 3, "https://github.com/a/b/releases/download/v1/demo.zip"
    )
    assert not failed.success and failed.error == "bad archive"
    monkeypatch.setattr(
        routes, "_download_release_asset", AsyncMock(side_effect=RuntimeError("network"))
    )
    with pytest.raises(RuntimeError, match="network"):
        await routes._secure_archive_analysis(
            db, user, 3, "https://github.com/a/b/releases/download/v1/demo.zip"
        )

    monkeypatch.setattr(
        routes, "_download_release_asset", AsyncMock(return_value=(str(archive), "d", 10))
    )
    monkeypatch.setattr(routes, "_archive_entries", Mock(side_effect=RuntimeError("malformed")))
    failed = await routes._secure_archive_analysis(
        db, user, 3, "https://github.com/a/b/releases/download/v1/demo.zip"
    )
    assert not failed.success and "safely inspected" in failed.error

    monkeypatch.setattr(
        "services.ai_access.authorized_server", AsyncMock(side_effect=AgentAccessDenied("denied"))
    )
    failed = await routes._secure_archive_analysis(
        db, user, 3, "https://github.com/a/b/releases/download/v1/demo.zip"
    )
    assert not failed.success and failed.error == "denied"


@pytest.mark.asyncio
async def test_github_simple_routes_and_error_mapping(monkeypatch):
    db = _db()
    user = _user()
    monkeypatch.setattr(routes, "enforce_agent_rate_limit", AsyncMock())
    search = AsyncMock(return_value={"query": "demo", "candidates": []})
    monkeypatch.setattr(routes, "search_github_plugins_service", search)
    assert (await routes.search_github_cs2_plugins("demo", 2, db=db, current_user=user))[
        "query"
    ] == "demo"
    monkeypatch.setattr(
        routes, "search_github_plugins_service", AsyncMock(side_effect=AgentAccessDenied("gone"))
    )
    with pytest.raises(Exception) as error:
        await routes.search_github_cs2_plugins("demo", 2, db=db, current_user=user)
    assert error.value.status_code == 404
    monkeypatch.setattr(
        routes, "search_github_plugins_service", AsyncMock(side_effect=GitHubPlanError("bad"))
    )
    with pytest.raises(Exception) as error:
        await routes.search_github_cs2_plugins("demo", 2, db=db, current_user=user)
    assert error.value.status_code == 409

    inspect = AsyncMock(return_value={"repo_url": "https://github.com/a/b"})
    monkeypatch.setattr(routes, "inspect_github_plugin_service", inspect)
    request = GitHubPluginInspectRequest(repo_url="https://github.com/a/b")
    assert (await routes.inspect_github_plugin(request, db, user))["repo_url"]
    monkeypatch.setattr(
        routes, "inspect_github_plugin_service", AsyncMock(side_effect=GitHubPlanError("no"))
    )
    with pytest.raises(Exception) as error:
        await routes.inspect_github_plugin(request, db, user)
    assert error.value.status_code == 409

    plan = AsyncMock(return_value={"server_id": 3, "repo_url": "https://github.com/a/b"})
    monkeypatch.setattr(routes, "build_github_install_plan", plan)
    plan_request = GitHubPluginInstallPlanRequest(repo_url="https://github.com/a/b")
    assert (await routes.plan_github_plugin_install(3, plan_request, db, user))["server_id"] == 3
    monkeypatch.setattr(
        routes, "build_github_install_plan", AsyncMock(side_effect=GitHubPlanError("bad plan"))
    )
    with pytest.raises(Exception) as error:
        await routes.plan_github_plugin_install(3, plan_request, db, user)
    assert error.value.status_code == 409

    execute = AsyncMock(return_value={"success": True, "message": "done"})
    monkeypatch.setattr(routes, "execute_github_install_plan", execute)
    execute_request = GitHubPluginInstallExecuteRequest(
        repo_url="https://github.com/a/b", expected_plan_hash="b" * 64
    )
    result = await routes.apply_github_plugin_install(3, execute_request, db, user)
    assert result["success"]
    assert execute.await_args.args[4] == "b" * 64
    monkeypatch.setattr(
        routes, "execute_github_install_plan", AsyncMock(side_effect=AgentAccessDenied("gone"))
    )
    with pytest.raises(Exception) as error:
        await routes.apply_github_plugin_install(3, execute_request, db, user)
    assert error.value.status_code == 404

    recipe = SimpleNamespace(
        id=9,
        repo_url="https://github.com/a/b",
        revision="main",
        source_prefix="x",
        target_prefix="addons",
    )
    monkeypatch.setattr(routes, "create_install_recipe", AsyncMock(return_value=recipe))
    recipe_request = GitHubInstallRecipeCreate(
        repo_url="https://github.com/a/b",
        display_name="Demo",
        source_prefix="x",
        target_prefix="addons",
    )
    assert (await routes.create_github_install_recipe(recipe_request, db, user))["id"] == 9
    monkeypatch.setattr(
        routes, "create_install_recipe", AsyncMock(side_effect=GitHubPlanError("recipe"))
    )
    with pytest.raises(Exception) as error:
        await routes.create_github_install_recipe(recipe_request, db, user)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_releases_ownership_and_install_wrapper(monkeypatch):
    db = _db()
    user = _user()
    assert not (await routes.get_github_releases("bad", db=db, current_user=user)).success
    monkeypatch.setattr(routes, "get_effective_github_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(routes.http_helper, "get", AsyncMock(return_value=(False, None, "offline")))
    failed = await routes.get_github_releases(
        "https://github.com/acme/demo", count=20, db=db, current_user=user
    )
    assert not failed.success and "Failed" in failed.error
    monkeypatch.setattr(
        routes.http_helper, "get", AsyncMock(return_value=(True, {"items": []}, None))
    )
    invalid = await routes.get_github_releases(
        "https://github.com/acme/demo", db=db, current_user=user
    )
    assert not invalid.success and "Unexpected" in invalid.error
    releases = [
        {"draft": True, "assets": []},
        {"prerelease": True, "assets": []},
        "invalid",
        {
            "id": 1,
            "tag_name": "v1",
            "name": "Demo",
            "assets": [
                {"name": "demo-windows.zip", "browser_download_url": "w"},
                {
                    "name": "demo-linux.zip",
                    "browser_download_url": "https://github.com/a/b/releases/download/v1/demo.zip",
                    "size": 2,
                },
                {"name": "readme.txt", "browser_download_url": "t"},
            ],
        },
    ]
    monkeypatch.setattr(routes.http_helper, "get", AsyncMock(return_value=(True, releases, None)))
    monkeypatch.setattr(
        "services.linux_runtime_service.annotate_runtime_assets", lambda assets, _profile: assets
    )
    result = await routes.get_github_releases(
        "https://github.com/acme/demo", count=20, db=db, current_user=user
    )
    assert (
        result.success
        and len(result.releases) == 1
        and result.releases[0].assets[0].name == "demo-linux.zip"
    )

    monkeypatch.setattr(routes.Server, "get_by_id", AsyncMock(return_value=_server()))
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=_server()))
    assert await routes.get_server_and_verify_ownership(db, 3, _user(is_admin=True))
    assert await routes.get_server_and_verify_ownership(db, 3, user)
    monkeypatch.setattr(routes.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(Exception) as error:
        await routes.get_server_and_verify_ownership(db, 3, user)
    assert error.value.status_code == 404

    missing = await routes.install_github_plugin(
        3, _install_request(installation_plan_hash=None), db, user
    )
    assert not missing.success
    monkeypatch.setattr(
        routes,
        "execute_github_install_plan",
        AsyncMock(return_value={"success": True, "message": "ok", "installed_files": 4}),
    )
    installed = await routes.install_github_plugin(3, _install_request(), db, user)
    assert installed.success and installed.installed_files == 4
    monkeypatch.setattr(
        routes, "execute_github_install_plan", AsyncMock(side_effect=GitHubPlanError("blocked"))
    )
    failed_install = await routes.install_github_plugin(3, _install_request(), db, user)
    assert not failed_install.success and failed_install.message == "blocked"


class _FileSSH:
    def __init__(self, *, connect=True, mode="ok"):
        self.connect_ok = connect
        self.mode = mode
        self.commands = []

    async def connect(self, _server):
        return self.connect_ok, "offline" if not self.connect_ok else "ok"

    async def disconnect(self):
        return None

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        if "test -d" in command:
            if self.mode == "missing":
                return True, "", ""
            return True, "exists", ""
        if "find . -type f" in command:
            if self.mode == "list-error":
                return False, "", "cannot list"
            return True, "12 ./demo.dll\nplain.cfg\n.\n", ""
        if "find . -type d" in command:
            return True, "./nested\n./demo.dll\n", ""
        if command.startswith("rm -rf"):
            return self.mode != "delete-error", "", "delete failed"
        return True, "", ""


@pytest.mark.asyncio
async def test_installed_analysis_and_uninstall_paths(monkeypatch):
    db = _db()
    user = _user()
    monkeypatch.setattr(
        routes, "get_server_and_verify_ownership", AsyncMock(return_value=_server())
    )
    ssh = _FileSSH()
    monkeypatch.setattr(routes, "SSHManager", lambda: ssh)
    result = await routes.analyze_installed_plugins(3, db=db, current_user=user)
    assert result.success and result.total_size == 12 and any(item.is_dir for item in result.files)
    bad_path = await routes.analyze_installed_plugins(
        3, directory="../etc", db=db, current_user=user
    )
    assert not bad_path.success and "Invalid" in bad_path.error
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH(mode="missing"))
    missing = await routes.analyze_installed_plugins(3, db=db, current_user=user)
    assert not missing.success and "does not exist" in missing.error
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH(mode="list-error"))
    listing = await routes.analyze_installed_plugins(3, db=db, current_user=user)
    assert not listing.success and "Failed to list" in listing.error
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH(connect=False))
    offline = await routes.analyze_installed_plugins(3, db=db, current_user=user)
    assert not offline.success and "SSH connection" in offline.error

    monkeypatch.setattr("services.deployment_progress.send_deployment_update", AsyncMock())
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH())
    request = PluginUninstallRequest(files_to_delete=["addons/demo.dll", "cfg/demo.cfg"])
    result = await routes.uninstall_plugin(3, request, db, user, None)
    assert result.success and result.deleted_files == 2
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH(mode="delete-error"))
    mixed = await routes.uninstall_plugin(3, request, db, user, None)
    assert not mixed.success and mixed.failed_files
    monkeypatch.setattr(routes, "SSHManager", lambda: _FileSSH(connect=False))
    offline = await routes.uninstall_plugin(3, request, db, user, None)
    assert not offline.success
