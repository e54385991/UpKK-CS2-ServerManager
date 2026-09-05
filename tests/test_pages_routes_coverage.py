"""覆盖服务端 HTML 页面路由的模板、权限和文件编辑分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import pages


@pytest.mark.asyncio
async def test_page_templates_and_legacy_redirect(monkeypatch):
    request = SimpleNamespace()
    rendered = []
    monkeypatch.setattr(pages, "maybe_redirect_legacy_html", lambda _request: None)
    monkeypatch.setattr(
        pages.templates, "TemplateResponse", lambda *args: rendered.append(args) or args
    )
    simple_pages = [
        (pages.root, "home.html", (request,)),
        (pages.deployment_tutorial_page, "deployment_tutorial.html", (request,)),
        (pages.login_page, "login.html", (request,)),
        (pages.register_page, "register.html", (request,)),
        (pages.google_callback_page, "google_callback.html", (request,)),
        (pages.servers_ui, "servers.html", (request,)),
        (pages.plugin_market_page, "plugin_market.html", (request, SimpleNamespace())),
        (pages.setup_wizard, "server_setup_wizard.html", (request, SimpleNamespace())),
        (pages.profile_page, "profile.html", (request, SimpleNamespace())),
        (pages.system_settings_page, "system_settings.html", (request, SimpleNamespace())),
        (pages.audit_logs_page, "audit_logs.html", (request, SimpleNamespace())),
        (pages.forgot_password_page, "forgot_password.html", (request,)),
        (pages.reset_password_page, "reset_password.html", (request,)),
    ]
    for route, template, args in simple_pages:
        result = await route(*args)
        assert result[1] == template
    redirect = object()
    monkeypatch.setattr(pages, "maybe_redirect_legacy_html", lambda _request: redirect)
    assert await pages.root(request) is redirect
    assert await pages.login_page(request) is redirect
    assert await pages.deployment_tutorial_page(request) is redirect
    assert await pages.register_page(request) is redirect
    assert await pages.servers_ui(request) is redirect
    assert await pages.plugin_market_page(request, SimpleNamespace()) is redirect
    assert await pages.setup_wizard(request, SimpleNamespace()) is redirect
    assert await pages.profile_page(request, SimpleNamespace()) is redirect
    assert await pages.system_settings_page(request, SimpleNamespace()) is redirect
    assert await pages.audit_logs_page(request, SimpleNamespace()) is redirect
    assert await pages.forgot_password_page(request) is redirect
    assert await pages.reset_password_page(request) is redirect


@pytest.mark.asyncio
async def test_page_permission_routes_and_console_types(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace()
    db = SimpleNamespace()
    server = SimpleNamespace(id=3, game_directory="/srv/cs2")
    monkeypatch.setattr(pages, "maybe_redirect_legacy_html", lambda _request: None)
    monkeypatch.setattr(pages.templates, "TemplateResponse", lambda *args: args)
    monkeypatch.setattr(
        pages.ServerResponse,
        "model_validate",
        classmethod(lambda _cls, _server: SimpleNamespace(model_dump_json=lambda: "{}")),
    )
    get_server = AsyncMock(return_value=server)
    monkeypatch.setattr(pages.servers, "get_server_with_permission", get_server)
    detail = await pages.server_detail_ui(request, 3, db, user)
    assert detail[1] == "server_detail.html" and "server_json" in detail[2]
    assert (await pages.console_popup(request, 3, "ssh", db, user))[2]["console_type"] == "SSH"
    assert (await pages.console_popup(request, 3, "game", db, user))[2]["console_type"] == "GAME"
    with pytest.raises(HTTPException) as error:
        await pages.console_popup(request, 3, "http", db, user)
    assert error.value.status_code == 404
    assert (await pages.ssh_console(request, 3, db, user))[1] == "ssh_console.html"
    assert (await pages.game_console(request, 3, db, user))[1] == "game_console.html"
    assert get_server.await_count >= 4

    redirect = object()
    monkeypatch.setattr(pages, "maybe_redirect_legacy_html", lambda _request: redirect)
    assert await pages.ssh_console(request, 3, db, user) is redirect


class _EditorSSH:
    def __init__(self, connect=True, valid=True, read=True):
        self.connect_ok = connect
        self.valid = valid
        self.read_ok = read
        self.disconnect = AsyncMock()

    async def connect(self, _server):
        return self.connect_ok, "offline" if not self.connect_ok else "ok"

    async def validate_path_within_base(self, *_args, **_kwargs):
        return self.valid, "outside" if not self.valid else ""

    async def read_file(self, *_args):
        return self.read_ok, "safe\\`content${x}" if self.read_ok else "", "read error"


@pytest.mark.asyncio
async def test_file_editor_connection_validation_and_read_paths(monkeypatch):
    request = SimpleNamespace()
    db = SimpleNamespace()
    user = SimpleNamespace()
    server = SimpleNamespace(id=3, game_directory="/srv/cs2")
    monkeypatch.setattr(pages, "maybe_redirect_legacy_html", lambda _request: None)
    monkeypatch.setattr(pages.servers, "get_server_with_permission", AsyncMock(return_value=server))
    monkeypatch.setattr(pages.templates, "TemplateResponse", lambda *args: args)
    current = _EditorSSH()
    monkeypatch.setattr("services.ssh_manager.SSHManager", lambda: current)
    result = await pages.file_editor_popup(
        request, 3, "/srv/cs2/cfg/demo.cfg", "demo.cfg", db, user
    )
    assert result[1] == "file_editor_popup.html" and "\\\\" in result[2]["file_content"]
    failed = _EditorSSH(connect=False)
    monkeypatch.setattr("services.ssh_manager.SSHManager", lambda: failed)
    with pytest.raises(HTTPException) as error:
        await pages.file_editor_popup(request, 3, "x", "x", db, user)
    assert error.value.status_code == 500
    invalid = _EditorSSH(valid=False)
    monkeypatch.setattr("services.ssh_manager.SSHManager", lambda: invalid)
    with pytest.raises(HTTPException) as error:
        await pages.file_editor_popup(request, 3, "x", "x", db, user)
    assert error.value.status_code == 403
    unreadable = _EditorSSH(read=False)
    monkeypatch.setattr("services.ssh_manager.SSHManager", lambda: unreadable)
    with pytest.raises(HTTPException) as error:
        await pages.file_editor_popup(request, 3, "x", "x", db, user)
    assert error.value.status_code == 500
