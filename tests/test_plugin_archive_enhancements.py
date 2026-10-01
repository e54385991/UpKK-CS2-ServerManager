"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

import stat
import zipfile

import pytest

from services.github_plugin_plan_service import (
    GitHubPlanError,
    _apply_user_mapping,
    _archive_entries,
    _detect_mapping,
    _infer_plugin_metadata,
    _is_linux_archive,
    _mapped_files,
    _safe_entry_name,
    _validate_release_contents,
    normalize_public_repo_url,
)
from services.plugin_installation import (
    _remote_plugin_temp_dir,
)
from tests.ai_agent_enhancements_fakes import (
    _startup_server as _startup_server,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupDB as _StartupDB,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupLock as _StartupLock,
)


def test_plugin_staging_directory_isolated_per_operation():
    first = _remote_plugin_temp_dir(32, "run/one")
    second = _remote_plugin_temp_dir(32, "run/two")

    assert first != second
    assert first == "/tmp/upkk-plugin-32-run-one"
    assert second == "/tmp/upkk-plugin-32-run-two"
    assert "github_plugin_32" not in first


@pytest.mark.parametrize(
    "value",
    (
        "http://github.com/owner/repo",
        "https://user@github.com/owner/repo",
        "https://github.com/owner/repo?asset=1",
        "https://127.0.0.1/owner/repo",
        "https://github.com/owner/repo/releases/latest",
        "https://github.com/owner/%2e%2e",
    ),
)
def test_github_repository_normalization_rejects_noncanonical_urls(value):
    with pytest.raises(GitHubPlanError):
        normalize_public_repo_url(value)


def test_github_repository_normalization_returns_canonical_identity():
    assert normalize_public_repo_url("https://github.com/KZGlobalTeam/cs2kz-metamod.git") == (
        "KZGlobalTeam",
        "cs2kz-metamod",
        "https://github.com/KZGlobalTeam/cs2kz-metamod",
    )


@pytest.mark.parametrize(
    "name",
    ("../escape", "/etc/passwd", "C:/Windows/file", "safe/../../escape", "safe\nfile"),
)
def test_archive_entry_names_reject_escape_and_control_characters(name):
    with pytest.raises(GitHubPlanError):
        _safe_entry_name(name)


def test_zip_archive_rejects_links_case_collisions_and_bombs(tmp_path):
    link_archive = tmp_path / "link.zip"
    with zipfile.ZipFile(link_archive, "w") as archive:
        link = zipfile.ZipInfo("addons/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(GitHubPlanError, match="links"):
        _archive_entries(str(link_archive), "plugin-linux.zip", link_archive.stat().st_size)

    collision_archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(collision_archive, "w") as archive:
        archive.writestr("addons/Plugin.dll", "one")
        archive.writestr("addons/plugin.dll", "two")
    with pytest.raises(GitHubPlanError, match="case-colliding"):
        _archive_entries(
            str(collision_archive), "plugin-linux.zip", collision_archive.stat().st_size
        )

    bomb_archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("addons/repeated.bin", b"0" * (1024 * 1024))
    with pytest.raises(GitHubPlanError, match="compression ratio"):
        _archive_entries(str(bomb_archive), "plugin-linux.zip", bomb_archive.stat().st_size)


def test_csgo_wrappers_flat_css_and_config_mapping_are_deterministic():
    wrapped = [
        {"path": "package/csgo/addons/metamod/plugin.vdf", "size": 4, "is_dir": False},
        {"path": "package/csgo/cfg/plugin.cfg", "size": 4, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(wrapped, "plugin")
    assert prefix == "package/csgo"
    assert required is False
    mapped = _mapped_files(wrapped, mapping)
    assert [item["target_path"] for item in mapped] == [
        "addons/metamod/plugin.vdf",
        "cfg/plugin.cfg",
    ]
    assert mapped[1]["file_role"] == "config"

    flat = [
        {"path": "Example.dll", "size": 4, "is_dir": False},
        {"path": "Example.deps.json", "size": 4, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(flat, "Example", "counterstrikesharp")
    assert prefix is None
    assert required is False
    assert mapping[0]["target"] == "addons/counterstrikesharp/plugins/Example"


def test_user_mapping_resolves_ambiguous_archive_layout():
    entries = [
        {"path": "payload/plugin.dll", "size": 4, "is_dir": False},
        {"path": "payload", "size": 0, "is_dir": True},
    ]
    prefix, mapping, required = _detect_mapping(entries, "mystery")
    assert required is True
    source, mapped = _apply_user_mapping(entries, "payload", "addons/counterstrikesharp/plugins")
    assert source == "payload"
    assert mapped == [{"source": "payload", "target": "addons/counterstrikesharp/plugins"}]


def test_user_mapping_rejects_missing_source_and_unsafe_target():
    entries = [{"path": "payload/plugin.dll", "size": 4, "is_dir": False}]
    with pytest.raises(GitHubPlanError, match="not found"):
        _apply_user_mapping(entries, "missing", "addons")
    with pytest.raises(GitHubPlanError, match="addons or cfg"):
        _apply_user_mapping(entries, "payload", "opt/cs2")


def test_stable_linux_asset_filter_prefers_installable_release_archives():
    assert _is_linux_archive("cs2kz-linux-master.tar.gz") is True
    assert _is_linux_archive("cs2kz-linux-master-upgrade.tar.gz") is True
    assert _is_linux_archive("plugin-windows.zip") is False
    assert _is_linux_archive("plugin-debug-linux.zip") is False
    assert _is_linux_archive("Source code.zip") is False


@pytest.mark.parametrize(
    "path",
    ("addons/plugin/install.sh", "addons/plugin/build.vcxproj", "src/plugin.csproj"),
)
def test_release_content_rejects_scripts_builds_and_debug_artifacts(path):
    with pytest.raises(GitHubPlanError, match="source-build"):
        _validate_release_contents([{"path": path, "is_dir": False}])


def test_release_content_allows_pdb_files_common_in_cs2_plugin_releases():
    # .pdb files are commonly shipped in CS2 plugin releases as debug symbols
    _validate_release_contents(
        [{"path": "addons/counterstrikesharp/plugins/Plugin/Plugin.pdb", "is_dir": False}]
    )


def test_metamod_layout_detection_maps_root_metamod_dir_to_addons():
    entries = [
        {"path": "cleanercs2/", "size": 0, "is_dir": True},
        {"path": "cleanercs2/cleanercs2.so", "size": 100, "is_dir": False},
        {"path": "cleanercs2/config.cfg", "size": 50, "is_dir": False},
        {"path": "metamod/", "size": 0, "is_dir": True},
        {"path": "metamod/cleanercs2.vdf", "size": 80, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(entries, "CleanerCS2")
    assert required is False
    assert mapping == [{"source": ".", "target": "addons"}]
    mapped = _mapped_files(entries, mapping)
    targets = {item["target_path"] for item in mapped}
    assert "addons/metamod/cleanercs2.vdf" in targets
    assert "addons/cleanercs2/cleanercs2.so" in targets
    assert "addons/cleanercs2/config.cfg" in targets


def test_plugins_root_dir_maps_to_counterstrikesharp_plugins():
    entries = [
        {"path": "plugins/", "size": 0, "is_dir": True},
        {"path": "plugins/Killfeed_Icons/", "size": 0, "is_dir": True},
        {"path": "plugins/Killfeed_Icons/Killfeed_Icons.dll", "size": 12288, "is_dir": False},
    ]
    prefix, mapping, required = _detect_mapping(entries, "killfeed-icons", "counterstrikesharp")
    assert required is False
    assert prefix == "plugins"
    assert mapping == [{"source": "plugins", "target": "addons/counterstrikesharp/plugins"}]
    mapped = _mapped_files(entries, mapping)
    assert len(mapped) == 1
    assert (
        mapped[0]["target_path"]
        == "addons/counterstrikesharp/plugins/Killfeed_Icons/Killfeed_Icons.dll"
    )


def test_counterstrikesharp_tree_maps_shared_and_plugin_files_together():
    entries = [
        {
            "path": "counterstrikesharp/shared/AdminApi/AdminApi.dll",
            "size": 100,
            "is_dir": False,
        },
        {
            "path": "counterstrikesharp/plugins/CS2-SimpleAdmin/CS2-SimpleAdmin.dll",
            "size": 200,
            "is_dir": False,
        },
        {
            "path": "counterstrikesharp/plugins/CS2-SimpleAdmin/CS2-SimpleAdmin.deps.json",
            "size": 50,
            "is_dir": False,
        },
    ]

    prefix, mapping, required = _detect_mapping(entries, "CS2-SimpleAdmin")

    assert required is False
    assert prefix == "counterstrikesharp"
    assert mapping == [{"source": "counterstrikesharp", "target": "addons/counterstrikesharp"}]
    targets = {item["target_path"] for item in _mapped_files(entries, mapping)}
    assert "addons/counterstrikesharp/shared/AdminApi/AdminApi.dll" in targets
    assert "addons/counterstrikesharp/plugins/CS2-SimpleAdmin/CS2-SimpleAdmin.dll" in targets
    metadata = _infer_plugin_metadata(entries, {"readme": ""})
    assert metadata["framework"] == "counterstrikesharp"


def test_single_compiled_plugin_wrapper_maps_to_repository_plugin_directory():
    entries = [
        {"path": "SimpleAdmin/SimpleAdmin.dll", "size": 100, "is_dir": False},
        {"path": "SimpleAdmin/SimpleAdmin.deps.json", "size": 50, "is_dir": False},
    ]

    prefix, mapping, required = _detect_mapping(entries, "SimpleAdmin", "counterstrikesharp")

    assert required is False
    assert prefix == "SimpleAdmin"
    assert mapping == [
        {
            "source": "SimpleAdmin",
            "target": "addons/counterstrikesharp/plugins/SimpleAdmin",
        }
    ]
