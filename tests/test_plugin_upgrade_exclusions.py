"""Upgrade-mode exclusion globs stay aligned with the web installer."""

from types import SimpleNamespace

from services.plugin_conflict_service import _asset_from_download_url
from services.plugins.upgrade_exclusions import (
    CONFIG_FILE_EXTENSIONS,
    apply_upgrade_mode_exclusions,
)


def test_upgrade_mode_appends_config_globs():
    result = apply_upgrade_mode_exclusions(["addons/keep.cfg"])
    assert result[0] == "addons/keep.cfg"
    assert "*.ini" in result
    assert "*.json" in result
    assert len(result) == 1 + len(CONFIG_FILE_EXTENSIONS)


def test_asset_from_download_url_parses_github_release():
    plugin = SimpleNamespace(custom_install_path=None, title="MatchZy")
    asset = _asset_from_download_url(
        plugin,
        "https://github.com/shobhit-pathak/MatchZy/releases/download/v0.8.1/MatchZy-linux.zip",
    )
    assert asset["release_tag"] == "v0.8.1"
    assert asset["release_id"] == "tag:v0.8.1"
    assert asset["asset_name"] == "MatchZy-linux.zip"
    assert asset["allowed_roots"] == ["addons", "cfg"]
