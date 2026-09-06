"""Stable public-contract baselines for compatibility-preserving refactors."""

from __future__ import annotations

import importlib
import inspect
import json
from difflib import unified_diff
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from starlette.routing import Match

import main
import modules
import services
from api.application import create_app
from api.routes import actions, file_manager, servers
from services.ssh_manager import SSHManager

BASELINE_DIRECTORY = Path(__file__).with_name("baselines")


def _load_json(name: str):
    return json.loads((BASELINE_DIRECTORY / name).read_text(encoding="utf-8"))


def _assert_contract(actual: object, name: str) -> None:
    expected = _load_json(name)
    if actual == expected:
        return
    actual_text = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    expected_text = json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    diff = "\n".join(
        unified_diff(expected_text, actual_text, fromfile=f"baseline/{name}", tofile="actual")
    )
    pytest.fail(
        f"{name} differs from the checked-in compatibility contract. "
        f"If this change is intentional, run `uv run python scripts/update_contract_baselines.py`.\n{diff[:12000]}"
    )


def _iter_registered_routes(routes):
    """Expand included routers through FastAPI's public traversal API."""
    for context in iter_route_contexts(routes):
        yield context.route


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


def _first_http_route_name(app, path: str, method: str = "GET") -> str | None:
    scope = {"type": "http", "path": path, "method": method}
    for context in iter_route_contexts(app.routes):
        match, _ = context.matches(scope)
        if match is Match.FULL:
            return context.name
    return None


def test_openapi_contract_matches_the_pre_refactor_baseline():
    _assert_contract(create_app(lifespan=None).openapi(), "openapi.json")


def test_route_registration_order_matches_the_pre_refactor_baseline():
    actual = _route_manifest(create_app(lifespan=None))

    _assert_contract(actual, "routes.json")
    assert [route for route in actual if route["kind"] == "APIWebSocketRoute"] == [
        {
            "kind": "APIWebSocketRoute",
            "path": "/api/ai/runs/{run_id}/events",
            "name": "ai_run_events",
            "methods": [],
        },
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
        {
            "kind": "APIWebSocketRoute",
            "path": "/api/v1/servers/{server_id}/console/ssh",
            "name": "ssh_console_websocket",
            "methods": [],
        },
        {
            "kind": "APIWebSocketRoute",
            "path": "/api/v1/servers/{server_id}/console/game",
            "name": "game_console_websocket",
            "methods": [],
        },
    ]


def test_composed_domain_routers_follow_their_declared_endpoint_order():
    for route_module in (actions, file_manager, servers):
        actual = [route.name for route in _iter_registered_routes(route_module.router.routes)]
        assert actual == list(route_module.ENDPOINT_ORDER)


def test_static_server_routes_match_before_the_server_id_route():
    app = create_app(lifespan=None)

    assert _first_http_route_name(app, "/servers/admin/all") == "list_all_servers_admin"
    assert _first_http_route_name(app, "/servers/disk-space-all") == "get_all_servers_disk_space"


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
