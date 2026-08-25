"""Regression coverage for the modular FastAPI application assembly."""

from pathlib import Path

import main
from api.application import create_app
from api.routes import health, pages
from api.templating import STATIC_DIRECTORY, templates

LEGACY_PAGE_PATHS = {
    "/",
    "/deployment-tutorial",
    "/forgot-password",
    "/google-callback",
    "/health",
    "/login",
    "/plugin-market",
    "/profile",
    "/register",
    "/reset-password",
    "/servers-ui",
    "/servers-ui/{server_id}",
    "/servers/{server_id}/console-popup/{console_type}",
    "/servers/{server_id}/file-editor-popup",
    "/servers/{server_id}/game-console",
    "/servers/{server_id}/ssh-console",
    "/setup-wizard",
    "/system-settings",
    "/audit-logs",
}


def test_application_factory_preserves_the_public_contract():
    factory_app = create_app(lifespan=None)

    assert factory_app is not main.app
    assert factory_app.openapi() == main.app.openapi()
    assert LEGACY_PAGE_PATHS <= set(factory_app.openapi()["paths"])
    assert factory_app.state.templates is templates


def test_application_factory_instances_keep_dependency_overrides_isolated():
    first = create_app(lifespan=None)
    second = create_app(lifespan=None)

    dependency = object()
    first.dependency_overrides[dependency] = lambda: "first"

    assert dependency not in second.dependency_overrides


def test_main_keeps_legacy_endpoint_exports():
    assert main.file_editor_popup is pages.file_editor_popup
    assert main.audit_logs_page is pages.audit_logs_page
    assert main.health_check is health.health_check
    assert STATIC_DIRECTORY.is_absolute()


def test_template_and_static_paths_are_independent_of_working_directory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    factory_app = create_app(lifespan=None)

    assert templates.get_template("home.html") is not None
    static_mount = next(route for route in factory_app.routes if route.name == "static")
    assert Path(static_mount.app.directory) == STATIC_DIRECTORY
