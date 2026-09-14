"""Modular FastAPI application assembly stays JSON-only."""

from pathlib import Path

import main
from api.application import create_app
from api.routes import health
from api.templating import STATIC_DIRECTORY
from services.container import ServiceContainer, build_service_container

PUBLIC_API_PATHS = {
    "/health",
    "/api/v1/auth/me",
}


def test_application_factory_preserves_the_public_contract():
    factory_app = create_app(lifespan=None)

    assert factory_app is not main.app
    assert factory_app.openapi() == main.app.openapi()
    assert PUBLIC_API_PATHS <= set(factory_app.openapi()["paths"])
    assert "/login" not in factory_app.openapi()["paths"]
    assert "/servers-ui" not in factory_app.openapi()["paths"]
    assert "/google-callback" not in factory_app.openapi()["paths"]


def test_application_factory_instances_keep_dependency_overrides_isolated():
    first = create_app(lifespan=None)
    second = create_app(lifespan=None)

    dependency = object()
    first.dependency_overrides[dependency] = lambda: "first"

    assert dependency not in second.dependency_overrides
    assert first.state.services is not second.state.services


def test_application_factory_accepts_an_explicit_service_container():
    container = build_service_container()
    app = create_app(lifespan=None, container_factory=lambda: container)

    assert isinstance(app.state.services, ServiceContainer)
    assert app.state.services is container


def test_main_keeps_health_and_lifecycle_exports():
    assert main.health_check is health.health_check
    assert STATIC_DIRECTORY.is_absolute()


def test_static_directory_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    factory_app = create_app(lifespan=None)

    static_mount = next(route for route in factory_app.routes if route.name == "static")
    assert Path(static_mount.app.directory) == STATIC_DIRECTORY
