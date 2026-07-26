"""Stable public-contract baselines for compatibility-preserving refactors."""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import main
import modules
import services
from api.application import create_app
from api.routes import actions, file_manager, servers
from services.ssh_manager import SSHManager

BASELINE_DIRECTORY = Path(__file__).with_name("baselines")


def _load_json(name: str):
    return json.loads((BASELINE_DIRECTORY / name).read_text(encoding="utf-8"))


def _iter_registered_routes(routes):
    """Flatten FastAPI's lazy included-router wrappers in registration order."""
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_registered_routes(original_router.routes)
        else:
            yield route


def _route_manifest(app):
    return [
        {
            "kind": type(route).__name__,
            "path": getattr(route, "path", None),
            "name": getattr(route, "name", None),
            "methods": sorted(getattr(route, "methods", None) or []),
        }
        for route in _iter_registered_routes(app.routes)
    ]


def test_openapi_contract_matches_the_pre_refactor_baseline():
    assert create_app(lifespan=None).openapi() == _load_json("openapi.json")


def test_route_registration_order_matches_the_pre_refactor_baseline():
    actual = _route_manifest(create_app(lifespan=None))

    assert actual == _load_json("routes.json")
    assert [route for route in actual if route["kind"] == "APIWebSocketRoute"] == [
        {
            "kind": "APIWebSocketRoute",
            "path": "/servers/{server_id}/deployment-status",
            "name": "deployment_status_websocket",
            "methods": [],
        },
        {
            "kind": "APIWebSocketRoute",
            "path": "/servers/{server_id}/ssh-console",
            "name": "ssh_console_websocket",
            "methods": [],
        },
        {
            "kind": "APIWebSocketRoute",
            "path": "/servers/{server_id}/game-console",
            "name": "game_console_websocket",
            "methods": [],
        },
        {
            "kind": "APIWebSocketRoute",
            "path": "/api/setup/setup-progress/{session_id}",
            "name": "setup_progress_websocket",
            "methods": [],
        },
    ]


def test_composed_domain_routers_follow_their_declared_endpoint_order():
    for route_module in (actions, file_manager, servers):
        assert [route.name for route in route_module.router.routes] == list(
            route_module.ENDPOINT_ORDER
        )


def test_public_python_exports_remain_importable():
    baseline = _load_json("exports.json")

    assert list(main.__all__) == baseline["main"]
    assert list(modules.__all__) == baseline["modules"]
    assert list(services.__all__) == baseline["services"]

    for module_name, symbols in baseline["route_symbols"].items():
        module = importlib.import_module(module_name)
        for symbol in symbols:
            assert hasattr(module, symbol), f"{module_name}.{symbol} is missing"


def test_legacy_asgi_entrypoint_is_the_factory_contract():
    assert main.app.openapi() == create_app(lifespan=None).openapi()


def test_ssh_manager_public_method_signatures_are_stable():
    actual = {
        name: str(inspect.signature(member))
        for name, member in inspect.getmembers(SSHManager)
        if callable(member) and not name.startswith("_")
    }
    assert actual == _load_json("ssh_manager_api.json")
