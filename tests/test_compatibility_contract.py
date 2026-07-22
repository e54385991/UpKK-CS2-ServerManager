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
from services.ssh_manager import SSHManager

BASELINE_DIRECTORY = Path(__file__).with_name("baselines")


def _load_json(name: str):
    return json.loads((BASELINE_DIRECTORY / name).read_text(encoding="utf-8"))


def _route_manifest(app):
    return [
        {
            "kind": type(route).__name__,
            "path": getattr(route, "path", None),
            "name": getattr(route, "name", None),
            "methods": sorted(getattr(route, "methods", None) or []),
        }
        for route in app.routes
    ]


def test_openapi_contract_matches_the_pre_refactor_baseline():
    assert create_app(lifespan=None).openapi() == _load_json("openapi.json")


def test_route_registration_order_matches_the_pre_refactor_baseline():
    assert _route_manifest(create_app(lifespan=None)) == _load_json("routes.json")


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
