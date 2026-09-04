"""Cover GitHub plan validation and archive mapping without remote I/O."""

from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import github_plugin_plan_service as plans


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _entry(path, *, size=4, is_dir=False, sha256="abcd"):
    return {"path": path, "size": size, "is_dir": is_dir, "sha256": sha256}


def test_github_plan_url_headers_release_and_archive_helpers():
    assert plans.normalize_public_repo_url(" https://github.com/acme/demo.git/ ") == (
        "acme",
        "demo",
        "https://github.com/acme/demo",
    )
    for value in (
        "http://github.com/acme/demo",
        "https://github.com/acme/demo?x=1",
        "https://github.com/acme/demo/extra/path",
        "https://github.com/acme/bad name",
    ):
        with pytest.raises(plans.GitHubPlanError):
            plans.normalize_public_repo_url(value)
    assert plans._headers(None)["Accept"].endswith("json")
    assert plans._headers("secret")["Authorization"] == "Bearer secret"
    assert plans._headers(None, raw=True)["Accept"].endswith("raw+json")
    assert plans._panel_managed_framework("acme", "counterstrikesharp") == "CounterStrikeSharp"
    assert plans._panel_managed_framework("alliedmodders", "metamod-source") == "Metamod:Source"
    assert plans._panel_managed_framework("acme", "demo") is None
    assert plans._post_install_restart_payload(False) == {"restart_required": False}
    assert plans._post_install_restart_payload(True)["restart_required"] is True
    original = {"already_installed": [1], "x": 2}
    assert plans._github_plan_confirmation_payload(original) == {"x": 2}

    assert plans._is_linux_archive("demo-linux.zip")
    assert not plans._is_linux_archive("demo-windows.zip")
    payload = plans._release_payload(
        {
            "id": 1,
            "tag_name": "v1",
            "assets": [
                {"id": 2, "name": "demo-linux.zip", "browser_download_url": "https://x/a.zip"},
                {"name": "demo-debug.zip"},
            ],
        }
    )
    assert payload["assets"][0]["url"] == "https://x/a.zip"
    with pytest.raises(plans.GitHubPlanError):
        plans._release_payload({"prerelease": True})


def test_archive_readers_and_safety_rules_use_tmp_path(tmp_path):
    zip_path = tmp_path / "demo.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("addons/plugin.dll", b"plugin")
        archive.writestr("cfg/server.cfg", b"sv_cheats 0")
    entries = plans._zip_entries(str(zip_path))
    assert {item["path"] for item in entries} == {"addons/plugin.dll", "cfg/server.cfg"}
    assert all(item["sha256"] for item in entries)

    tar_path = tmp_path / "demo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        data = b"hello"
        info = tarfile.TarInfo("addons/plugin.dll")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    tar_entries = plans._tar_entries(str(tar_path))
    assert tar_entries[0]["path"] == "addons/plugin.dll"

    assert plans._archive_entries(str(zip_path), "demo.zip", 100)[0]["path"] == "addons/plugin.dll"
    with pytest.raises(plans.GitHubPlanError, match="Unsafe archive path"):
        plans._safe_entry_name("../secret")
    with pytest.raises(plans.GitHubPlanError, match="case-colliding"):
        plans._validate_archive_entries([_entry("A.txt"), _entry("a.txt")], 100)
    with pytest.raises(plans.GitHubPlanError, match="too many"):
        plans._validate_archive_entries([_entry("x")] * (plans.MAX_ARCHIVE_ENTRIES + 1), 100)
    with pytest.raises(plans.GitHubPlanError, match="source-build"):
        plans._validate_release_contents([_entry("build.sh")])
    plans._validate_release_contents([_entry("addons/plugin.dll")])


def test_archive_mapping_inference_and_projection_branches():
    prefix, mapping, required = plans._detect_mapping(
        [_entry("root/addons/p.dll"), _entry("root/cfg/a.cfg")], "demo"
    )
    assert (prefix, required) == ("root", False) and len(mapping) == 2
    prefix, mapping, _ = plans._detect_mapping([_entry("counterstrikesharp/plugins/p.dll")], "demo")
    assert prefix == "counterstrikesharp" and mapping[0]["target"].startswith("addons/")
    prefix, mapping, _ = plans._detect_mapping([_entry("plugins/p.dll")], "demo")
    assert mapping[0]["target"].endswith("plugins")
    prefix, mapping, _ = plans._detect_mapping([_entry("p.dll"), _entry("p.deps.json")], "my demo")
    assert mapping[0]["target"].endswith("my-demo")
    prefix, mapping, _ = plans._detect_mapping(
        [_entry("metamod", is_dir=True), _entry("metamod/plugin.vdf")], "demo"
    )
    assert mapping == [{"source": ".", "target": "addons"}]
    assert plans._detect_mapping([_entry("readme.txt")], "demo")[2] is True

    assert plans._apply_user_mapping([_entry("payload/a.dll")], "payload", "addons/demo")[0] == "payload"
    with pytest.raises(plans.GitHubPlanError, match="target_prefix"):
        plans._apply_user_mapping([], None, "tmp")
    with pytest.raises(plans.GitHubPlanError, match="source_prefix"):
        plans._apply_user_mapping([_entry("other/a.dll")], "payload", "addons")

    metadata = plans._infer_plugin_metadata(
        [_entry("addons/counterstrikesharp/plugins/p.dll"), _entry("addons/metamod/p.vdf")],
        {"readme": "Metamod and CounterStrikeSharp"},
    )
    assert metadata["framework"] == "counterstrikesharp"
    assert len(metadata["dependencies"]) == 1 and len(metadata["documentation_hints"]) == 2
    mapped = plans._mapped_files(
        [_entry("addons/plugin.cfg"), _entry("addons/gamedata/data.json")],
        [{"source": "addons", "target": "addons"}],
    )
    assert [item["file_role"] for item in mapped] == ["config", "gamedata"]
    with pytest.raises(plans.GitHubPlanError, match="duplicate"):
        plans._mapped_files(
            [_entry("x/a.dll"), _entry("y/a.dll")],
            [{"source": "x", "target": "addons"}, {"source": "y", "target": "addons"}],
        )


@pytest.mark.asyncio
async def test_github_inspect_and_search_handle_release_and_documentation_variants(monkeypatch):
    monkeypatch.setattr(plans, "get_effective_github_token", AsyncMock(return_value="token"))
    request = AsyncMock(
        side_effect=[
            {
                "full_name": "acme/demo",
                "description": "Demo",
                "stargazers_count": 4,
                "topics": ["cs2"],
            },
            {
                "id": 1,
                "tag_name": "v1",
                "name": "Release",
                "assets": [
                    {"name": "demo-linux.zip", "browser_download_url": "https://x/demo.zip"},
                    {"name": "demo-linux.tar.gz", "browser_download_url": "https://x/demo.tar.gz"},
                ],
                "body": "notes",
            },
            "# README\nUse this plugin",
        ]
    )
    monkeypatch.setattr(plans, "_github_request", request)
    monkeypatch.setattr(
        "services.linux_runtime_service.annotate_runtime_assets",
        lambda assets, _profile: assets,
    )
    monkeypatch.setattr(
        "services.linux_runtime_service.select_unique_runtime_asset",
        lambda assets, _profile: assets[0],
    )
    inspected = await plans.inspect_github_plugin(
        object(), SimpleNamespace(id=1), "https://github.com/acme/demo", mode="install"
    )
    assert inspected["selected_asset"]["name"] == "demo-linux.zip"
    assert inspected["documentation"]["untrusted"] is True

    request.side_effect = [
        {"private": True},
    ]
    with pytest.raises(plans.GitHubPlanError, match="Private"):
        await plans.inspect_github_plugin(object(), SimpleNamespace(id=1), "https://github.com/acme/demo")

    request.side_effect = [
        {"full_name": "acme/demo"},
        {"id": 1, "tag_name": "v1", "assets": [{"name": "demo.zip"}]},
        plans.GitHubPlanError("no readme"),
    ]
    # The release asset has no URL and the README failure is intentionally downgraded to a warning.
    inspected = await plans.inspect_github_plugin(object(), SimpleNamespace(id=1), "https://github.com/acme/demo")
    assert any("README" in warning for warning in inspected["warnings"])

    monkeypatch.setattr(
        plans,
        "_github_request",
        AsyncMock(return_value={"items": [{"html_url": "https://github.com/acme/demo"}]}),
    )
    monkeypatch.setattr(
        plans,
        "inspect_github_plugin",
        AsyncMock(
            side_effect=[
                {
                    "repo_url": "https://github.com/acme/demo",
                    "repository": {"full_name": "acme/demo", "description": "demo", "stars": 2, "pushed_at": "1"},
                    "release": {"assets": [1], "tag": "v1", "published_at": "2"},
                    "selected_asset": {"name": "demo.zip"},
                }
            ]
        ),
    )
    results = await plans.search_github_plugins(object(), SimpleNamespace(id=1), " cs2 ", limit=1)
    assert results["recommended_repo_url"] == "https://github.com/acme/demo"
    with pytest.raises(plans.GitHubPlanError, match="1 to 120"):
        await plans.search_github_plugins(object(), SimpleNamespace(id=1), "   ")


@pytest.mark.asyncio
async def test_github_plan_revision_and_layout_download_are_isolated(monkeypatch, tmp_path):
    class _SSH:
        async def connect(self, _server):
            return True, "ok"

        async def execute_command(self, _command, **_kwargs):
            return True, "addons/p.dll\tdeadbeef\n", ""

        async def disconnect(self):
            return None

    monkeypatch.setattr(plans, "SSHManager", lambda: _SSH())
    revisions = await plans._target_revisions(
        SimpleNamespace(game_directory="/srv/cs2"), [{"target_path": "addons/p.dll"}]
    )
    assert revisions["addons/p.dll"] == "deadbeef"

    archive_path = tmp_path / "layout.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("addons/p.dll", b"x")
    monkeypatch.setattr(
        plans,
        "_download_release_asset",
        AsyncMock(return_value=(str(archive_path), "hash", 10)),
    )
    layout = await plans.inspect_release_asset_layout(
        {"url": "https://x/layout.zip", "name": "layout.zip"}, "demo"
    )
    assert layout["archive_sha256"] == "hash" and layout["mapping_required"] is False

    bad_manager = SimpleNamespace(connect=AsyncMock(return_value=(False, "offline")))
    monkeypatch.setattr(plans, "SSHManager", lambda: bad_manager)
    with pytest.raises(plans.GitHubPlanError, match="revisioning"):
        await plans._target_revisions(SimpleNamespace(game_directory="/srv/cs2"), [])


def test_github_archive_edge_cases_and_mappings(tmp_path):
    for value in ("", "/absolute", "C:\\file", "a/../b", "a\x00b", "a\nb"):
        with pytest.raises(plans.GitHubPlanError):
            plans._safe_entry_name(value)
    assert plans._safe_entry_name("./addons/plugin.dll") == "addons/plugin.dll"
    with pytest.raises(plans.GitHubPlanError, match="expanded"):
        plans._validate_archive_entries([_entry("x", size=plans.MAX_EXPANDED_BYTES + 1)], 1)
    with pytest.raises(plans.GitHubPlanError, match="compression"):
        plans._validate_archive_entries([_entry("x", size=201)], 1)
    with pytest.raises(plans.GitHubPlanError, match="Unsupported"):
        plans._archive_entries(str(tmp_path / "x.bin"), "x.bin", 1)

    zip_path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("addons/link")
    info.external_attr = stat.S_IFLNK << 16
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(plans.GitHubPlanError, match="special"):
        plans._zip_entries(str(zip_path))

    tar_path = tmp_path / "link.tar"
    with tarfile.open(tar_path, "w") as archive:
        link = tarfile.TarInfo("addons/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        archive.addfile(link)
    with pytest.raises(plans.GitHubPlanError, match="links"):
        plans._tar_entries(str(tar_path))

    entries = [_entry("addons/plugin.dll"), _entry("cfg/server.json"), _entry("addons/gamedata/x.json")]
    mapped = plans._mapped_files(entries, [{"source": ".", "target": "addons"}])
    assert {item["file_role"] for item in mapped} == {"data", "config", "gamedata"}
    with pytest.raises(plans.GitHubPlanError, match="more than"):
        plans._mapped_files([_entry(f"x/{i}.dll") for i in range(plans.MAX_AUTOMATIC_FILES + 1)], [{"source": "x", "target": "addons"}])


@pytest.mark.asyncio
async def test_github_request_target_recipe_and_inspect_errors(monkeypatch, tmp_path):
    class _Response:
        def __init__(self, status_code, text="body", payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {"ok": True}

        def json(self):
            return self._payload

    class _Client:
        response = _Response(200, payload={"ok": True})

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return self.response

    monkeypatch.setattr(plans.httpx, "AsyncClient", _Client)
    assert await plans._github_request("/repos/acme/demo", "token") == {"ok": True}
    _Client.response = _Response(200, text="raw")
    assert await plans._github_request("/readme", None, raw=True) == "raw"
    for status in (404, 500):
        _Client.response = _Response(status)
        with pytest.raises(plans.GitHubPlanError):
            await plans._github_request("/repos/acme/demo", None)
    with pytest.raises(plans.GitHubPlanError, match="path"):
        await plans._github_request("//bad", None)

    class _SSH:
        async def connect(self, _server):
            return True, "ok"

        async def execute_command(self, _command, **_kwargs):
            return True, "addons/p.dll\tsymlink\n", ""

        async def disconnect(self):
            return None

    monkeypatch.setattr(plans, "SSHManager", lambda: _SSH())
    with pytest.raises(plans.GitHubPlanError, match="regular"):
        await plans._target_revisions(SimpleNamespace(game_directory="/srv"), [{"target_path": "addons/p.dll"}])

    class _Db:
        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    with pytest.raises(plans.GitHubPlanError, match="recipe"):
        await plans._recipe_for_plan(_Db(), SimpleNamespace(id=1), "https://github.com/a/b", 1)
    assert await plans._recipe_for_plan(_Db(), SimpleNamespace(id=1), "https://github.com/a/b", None) is None

    archive = tmp_path / "bad.zip"
    monkeypatch.setattr(plans, "_download_release_asset", AsyncMock(return_value=(str(archive), "h", 2)))
    monkeypatch.setattr(plans, "_archive_entries", lambda *_args: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(plans.GitHubPlanError, match="safely"):
        await plans.inspect_release_asset_layout({"url": "https://github.com/a/releases/download/v/a.zip", "name": "a.zip"}, "a")


def test_github_install_plan_validation_and_recipe_creation(monkeypatch):
    base = {
        "mapping_required": False,
        "plan_hash": "h" * 64,
        "hard_conflicts": [],
        "conflict_warnings": [],
        "compatibility_unknown": False,
    }
    expected_hash = "h" * 64
    assert plans._validate_github_install_plan(base, expected_hash, None, False) == set()
    for changes, message in (
        ({"mapping_required": True}, "mapping"),
        ({"plan_hash": "other"}, "changed"),
        ({"hard_conflicts": [{"rule_id": 4}]}, "hard conflict"),
        ({"conflict_warnings": [{"rule_id": 5}]}, "acknowledgement"),
        ({"compatibility_unknown": True}, "unknown"),
    ):
        current = {**base, **changes}
        with pytest.raises(plans.GitHubPlanError, match=message):
            plans._validate_github_install_plan(current, expected_hash, None, False)
    warning = {**base, "conflict_warnings": [{"rule_id": 5}]}
    assert plans._validate_github_install_plan(warning, expected_hash, {5}, False) == {5}
    unknown = {**base, "compatibility_unknown": True}
    assert plans._validate_github_install_plan(unknown, expected_hash, set(), True) == set()

    class _Db:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()
            self.refresh = AsyncMock()

        def add(self, value):
            self.added.append(value)

    import asyncio

    db = _Db()
    recipe = asyncio.run(
        plans.create_install_recipe(
            db,
            SimpleNamespace(id=2, is_admin=True),
            {
                "repo_url": "https://github.com/acme/demo",
                "source_prefix": "payload",
                "target_prefix": "addons",
                "display_name": " Demo ",
            },
        )
    )
    assert recipe.created_by == 2 and len(recipe.revision) == 64
    with pytest.raises(PermissionError):
        asyncio.run(plans.create_install_recipe(db, SimpleNamespace(id=2, is_admin=False), {"repo_url": "https://github.com/acme/demo", "target_prefix": "addons", "display_name": "x"}))


@pytest.mark.asyncio
async def test_build_github_install_plan_success_and_selection_errors(monkeypatch):
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest

    server = SimpleNamespace(id=7, user_id=3, game_directory="/srv/cs2")
    asset = {
        "name": "demo-linux.zip",
        "url": "https://github.com/acme/demo/releases/download/v1/demo-linux.zip",
        "runtime_compatibility": "alternative",
    }
    inspected = {
        "repo_url": "https://github.com/acme/demo",
        "release": {"id": "r1", "tag": "v1", "assets": [asset]},
        "selected_asset": asset,
        "documentation": {"readme": "CounterStrikeSharp"},
        "warnings": ["Multiple Linux release assets require an explicit selection"],
    }

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Db:
        async def execute(self, _query):
            return _Result()

    monkeypatch.setattr(plans, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value={"reason": "detected"}),
    )
    monkeypatch.setattr(plans, "inspect_github_plugin", AsyncMock(return_value=inspected))
    monkeypatch.setattr(
        plans,
        "inspect_release_asset_layout",
        AsyncMock(
            return_value={
                "archive_sha256": "a" * 64,
                "entries": [_entry("addons/counterstrikesharp/plugins/p.dll")],
                "source_prefix": "addons",
                "mapping": [{"source": "addons", "target": "addons"}],
                "mapping_required": False,
            }
        ),
    )
    monkeypatch.setattr(plans, "_target_revisions", AsyncMock(return_value={}))
    monkeypatch.setattr(plans, "_recipe_for_plan", AsyncMock(return_value=None))
    request = GitHubPluginInstallPlanRequest(
        repo_url="https://github.com/acme/demo",
        asset_name="demo-linux.zip",
        source_prefix="addons",
        target_prefix="addons",
    )
    plan = await plans.build_github_install_plan(_Db(), SimpleNamespace(id=3), 7, request)
    assert plan["plan_hash"] and plan["compatibility_unknown"]
    assert any("overrides" in item for item in plan["warnings"])

    with pytest.raises(plans.GitHubPlanError, match="managed"):
        await plans.build_github_install_plan(
            _Db(), SimpleNamespace(id=3), 7,
            GitHubPluginInstallPlanRequest(repo_url="https://github.com/roflmuffin/counterstrikesharp"),
        )
    inspected["selected_asset"] = None
    with pytest.raises(plans.GitHubPlanError, match="exactly one"):
        await plans.build_github_install_plan(
            _Db(), SimpleNamespace(id=3), 7,
            GitHubPluginInstallPlanRequest(repo_url="https://github.com/acme/demo"),
        )


@pytest.mark.asyncio
async def test_execute_github_install_plan_dependencies_and_managed_tracking(monkeypatch):
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest, GitHubPluginInstallResponse

    server = SimpleNamespace(id=7, user_id=3, game_directory="/srv/cs2")
    request = GitHubPluginInstallPlanRequest(
        repo_url="https://github.com/acme/demo", config_policy="preserve"
    )
    plan = {
        "mapping_required": False,
        "plan_hash": "h" * 64,
        "hard_conflicts": [],
        "conflict_warnings": [],
        "compatibility_unknown": False,
        "already_installed": [],
        "dependencies": [],
        "mapping": [{"source": "addons", "target": "addons"}],
        "source_prefix": "addons",
        "target_revisions": {},
        "recipe_id": None,
        "files": [{"target_path": "addons/p.cfg", "file_role": "config", "target_revision": "old", "sha256": "new"}],
        "exclude_dirs": [],
        "exclude_files": [],
        "asset": {"name": "demo-linux.zip", "url": "https://github.com/acme/demo/releases/download/v1/demo-linux.zip"},
        "repo_url": "https://github.com/acme/demo",
        "release_id": "r1",
        "release_tag": "v1",
        "archive_sha256": "a" * 64,
        "linux_runtime_profile": {"reason": "detected"},
    }

    class _Result:
        def __init__(self, scalar=None, rows=()):
            self.scalar = scalar
            self.rows = list(rows)

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return SimpleNamespace(all=lambda: self.rows)

    class _Db:
        def __init__(self, managed):
            self.managed = managed
            self.calls = 0
            self.added = []
            self.commit = AsyncMock()
            self.delete = AsyncMock()

        async def execute(self, _query):
            self.calls += 1
            return _Result(self.managed if self.calls == 1 else None, [])

        def add(self, value):
            self.added.append(value)

    managed = SimpleNamespace(id=11, install_recipe_id=None, installed_asset_name=None, archive_sha256=None, config_policy=None)
    db = _Db(managed)
    monkeypatch.setattr(plans, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(plans, "build_github_install_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(
        plans,
        "install_github_plugin_with_retry",
        AsyncMock(return_value=GitHubPluginInstallResponse(success=True, message="installed", installed_files=1)),
    )
    monkeypatch.setattr(plans, "_target_revisions", AsyncMock(return_value={}))
    monkeypatch.setattr("services.linux_runtime_service.steam_runtime_for_asset", lambda _name: "steamrt3")
    monkeypatch.setattr(plans.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    result = await plans.execute_github_install_plan(db, SimpleNamespace(id=3), 7, request, "h" * 64)
    assert result["success"] and result["restart_required"]
    assert managed.installed_asset_name == "demo-linux.zip" and db.added

    plan["dependencies"] = [{"id": 22}]
    monkeypatch.setattr(plans, "build_market_plan", AsyncMock(return_value={"plan_hash": "dep"}))
    monkeypatch.setattr(
        plans,
        "execute_market_plan",
        AsyncMock(return_value={"success": False, "restart_required": True}),
    )
    dependency = await plans._execute_github_install_plan_locked(
        db, SimpleNamespace(id=3), 7, request, "h" * 64
    )
    assert not dependency["success"] and dependency["restart_required"]

    plan["already_installed"] = [22]
    plan["dependencies"] = [{"id": 22}]
    dependency = await plans._execute_github_install_plan_locked(
        db, SimpleNamespace(id=3), 7, request, "h" * 64
    )
    assert dependency["success"]
