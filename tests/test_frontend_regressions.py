"""Regression checks for locally vendored frontend assets and Alpine contracts."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_icons_reference_the_served_font_directory():
    stylesheet = (PROJECT_ROOT / "static" / "css" / "bootstrap-icons.min.css").read_text(
        encoding="utf-8"
    )

    assert 'url("../fonts/bootstrap-icons.woff2?' in stylesheet
    assert 'url("../fonts/bootstrap-icons.woff?' in stylesheet
    assert 'url("fonts/' not in stylesheet
    assert (PROJECT_ROOT / "static" / "fonts" / "bootstrap-icons.woff2").is_file()
    assert (PROJECT_ROOT / "static" / "fonts" / "bootstrap-icons.woff").is_file()


def test_startup_command_preview_has_a_complete_alpine_contract():
    overview = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "overview_tab.html"
    ).read_text(encoding="utf-8")
    scripts = (PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html").read_text(
        encoding="utf-8"
    )

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
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["serverDetail"]
        assert keys <= messages.keys()


def test_manual_deployment_confirmation_is_available_and_localized():
    overview = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "overview_tab.html"
    ).read_text(encoding="utf-8")
    scripts = (PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html").read_text(
        encoding="utf-8"
    )

    assert "confirmDeploymentManually()" in overview
    assert "async confirmDeploymentManually()" in scripts
    assert "`/servers/${this.serverId}/confirm-deployment`" in scripts
    assert "data.error && Boolean(this.server.last_deployed)" in scripts

    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["serverDetail"]
        assert {
            "confirmDeploymentManually",
            "confirmDeploymentManuallyPrompt",
            "deploymentManuallyConfirmed",
            "deploymentManualConfirmFailed",
        } <= messages.keys()


def test_plugin_market_loads_admin_accessible_servers_for_plugin_management():
    template = (PROJECT_ROOT / "templates" / "plugin_market.html").read_text(encoding="utf-8")

    assert "async function getServersAvailableForPluginManagement()" in template
    assert "currentUser?.is_admin ? '/servers/admin/all' : '/servers'" in template
    assert template.count("getServersAvailableForPluginManagement()") == 3
    assert "formatPluginManagementServerName(server)" in template


def test_plugin_config_restore_supports_multiple_default_sources():
    script = (PROJECT_ROOT / "static" / "js" / "plugin-config-manager.js").read_text(
        encoding="utf-8"
    )

    assert "const restoredSources = response.sources || [response];" in script
    assert "await this.reloadSources(restoredSources[0]?.id);" in script


def test_alpine_components_do_not_run_automatic_init_twice():
    for template_name in ("server_detail.html", "servers.html", "server_setup_wizard.html"):
        template = (PROJECT_ROOT / "templates" / template_name).read_text(encoding="utf-8")
        assert 'x-init="init()"' not in template


def test_system_settings_support_global_github_fallback_without_echoing_secret():
    template = (PROJECT_ROOT / "templates" / "system_settings.html").read_text(encoding="utf-8")

    assert 'id="global-github-token"' in template
    assert "settings.has_global_github_token" in template
    assert "settings.global_github_token_prefix" in template
    assert ".value = settings.global_github_token" not in template
    assert "settings.default_proxy_mode || 'panel'" in template

    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["systemSettings"]
        assert {
            "globalGithubToken",
            "globalGithubTokenHelp",
            "globalGithubTokenConfigured",
            "globalGithubTokenNotConfigured",
            "clearGlobalGithubToken",
        } <= messages.keys()


def test_map_management_prompts_for_restart_after_every_config_mutation():
    map_script = (PROJECT_ROOT / "static" / "js" / "map-management.js").read_text(encoding="utf-8")
    server_template = (PROJECT_ROOT / "templates" / "server_detail.html").read_text(
        encoding="utf-8"
    )

    assert "confirmRestartAfterChange()" in map_script
    assert map_script.count("this.confirmRestartAfterChange();") == 8
    assert "new CustomEvent('map-restart-server')" in map_script
    assert "@map-restart-server.window=\"executeAction('restart')\"" in server_template

    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["mapManagement"]
        assert "restartRequiredConfirm" in messages


def test_plugin_config_tab_is_lazy_loaded_and_localized():
    server_template = (PROJECT_ROOT / "templates" / "server_detail.html").read_text(
        encoding="utf-8"
    )
    server_script = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "scripts.html"
    ).read_text(encoding="utf-8")
    tab_template = (
        PROJECT_ROOT / "templates" / "server_detail_includes" / "plugin_configs_tab.html"
    ).read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "js" / "plugin-config-manager.js").read_text(
        encoding="utf-8"
    )

    assert 'data-bs-target="#plugin-configs"' in server_template
    assert "new CustomEvent('plugin-configs-open')" in server_template
    assert '@plugin-configs-open.window="open()"' in tab_template
    assert "async open()" in script
    assert "if (this.initialized) return;" in script
    assert "/scan`" in script
    assert "response.body.getReader()" in script
    assert "await this.reloadSources(source.id)" in script
    assert "item.id === source.id && item.persisted" in script
    assert "async refreshSources()" in script
    assert "await this.reloadSources(this.activeSourceId);" in script
    assert "loadSource(source)" in tab_template
    assert "source.fileCount" in tab_template
    assert '@click="refreshSources()"' in tab_template
    assert "init()" not in script
    assert "button.addEventListener('shown.bs.tab'" in server_script
    assert "new CustomEvent('plugin-configs-open')" in server_script
    assert server_script.index("tabButtons.forEach") < server_script.index(
        "const savedTab = localStorage.getItem(storageKey);"
    )

    required = {
        "title",
        "manualLoadHint",
        "reloadSources",
        "sourcesReloaded",
        "addSource",
        "loadConfiguration",
        "visual",
        "raw",
        "conflict",
        "saved",
        "scanning",
        "persisted",
        "persistenceFailed",
        "streamInterrupted",
    }
    for locale in ("en-US", "zh-CN"):
        messages = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["pluginConfigs"]
        assert required <= messages.keys()
