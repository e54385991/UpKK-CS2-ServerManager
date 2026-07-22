"""Regression checks for locally vendored frontend assets and Alpine contracts."""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_icons_reference_the_served_font_directory():
    stylesheet = (
        PROJECT_ROOT / "static" / "css" / "bootstrap-icons.min.css"
    ).read_text(encoding="utf-8")

    assert 'url("../fonts/bootstrap-icons.woff2?' in stylesheet
    assert 'url("../fonts/bootstrap-icons.woff?' in stylesheet
    assert 'url("fonts/' not in stylesheet
    assert (PROJECT_ROOT / "static" / "fonts" / "bootstrap-icons.woff2").is_file()
    assert (PROJECT_ROOT / "static" / "fonts" / "bootstrap-icons.woff").is_file()


def test_startup_command_preview_has_a_complete_alpine_contract():
    overview = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "overview_tab.html"
    ).read_text(encoding="utf-8")
    scripts = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html"
    ).read_text(encoding="utf-8")

    for state in ("startupCommand", "loadingStartupCommand", "startupCommandError"):
        assert state in overview
        assert f"{state}:" in scripts

    assert "loadStartupCommand()" in overview
    assert "async loadStartupCommand()" in scripts
    assert "await this.loadStartupCommand();" in scripts
    assert "`/servers/${this.serverId}/startup-command`" in scripts
    assert "copyStartupCommand()" in overview
    assert "async copyStartupCommand()" in scripts
    assert "async writeClipboardText(text)" in scripts


def test_startup_command_messages_exist_in_all_locales():
    keys = {
        "startupCommand",
        "startupCommandLoadFailed",
        "startupCommandCopied",
        "startupCommandCopyFailed",
    }

    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )["serverDetail"]
        assert keys <= messages.keys()


def test_alpine_components_do_not_run_automatic_init_twice():
    for template_name in ("server_detail.html", "servers.html", "server_setup_wizard.html"):
        template = (PROJECT_ROOT / "templates" / template_name).read_text(
            encoding="utf-8"
        )
        assert 'x-init="init()"' not in template
