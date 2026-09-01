"""Structural API contract checks for the maintained versioned surface."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.routes.v1.schemas import (
    PluginCatalogImportRequest,
    ServerConfigImportRequest,
    ServerOperationRequest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _contract_checker():
    spec = importlib.util.spec_from_file_location(
        "check_api_contracts", PROJECT_ROOT / "scripts" / "check_api_contracts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_versioned_routes_have_explicit_safe_response_contracts():
    assert _contract_checker().check() == []


def test_v1_request_models_reject_unknown_fields():
    for model, payload in (
        (ServerOperationRequest, {"action": "status"}),
        (PluginCatalogImportRequest, {"plugins": []}),
        (ServerConfigImportRequest, {"servers": [{"name": "server"}]}),
    ):
        with pytest.raises(ValidationError):
            model(**payload, unexpected=True)


def test_operation_workers_do_not_capture_http_request_objects():
    runner = importlib.import_module("api.routes.v1.operation_runner")

    for name in ("run_server_operation", "run_plugin_install", "run_url_download"):
        assert "request" not in str(getattr(runner, name).__annotations__)
