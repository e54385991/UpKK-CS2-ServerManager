#!/usr/bin/env python3
"""Refresh intentional OpenAPI, route, and public-export compatibility baselines."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi.routing import iter_route_contexts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env.example", override=False)

import main
import modules
import services
from api.application import create_app

BASELINES = PROJECT_ROOT / "tests" / "baselines"


def write_json(name: str, value: object) -> None:
    (BASELINES / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main_script() -> None:
    app = create_app(lifespan=None)
    write_json("openapi.json", app.openapi())
    routes = [
        {
            "kind": type(context.route).__name__,
            "path": getattr(context.route, "path", None),
            "name": getattr(context.route, "name", None),
            "methods": sorted(getattr(context.route, "methods", None) or []),
        }
        for context in iter_route_contexts(app.routes)
    ]
    write_json("routes.json", routes)

    export_path = BASELINES / "exports.json"
    exports = json.loads(export_path.read_text(encoding="utf-8"))
    exports["main"] = list(main.__all__)
    exports["modules"] = list(modules.__all__)
    exports["services"] = list(services.__all__)
    write_json("exports.json", exports)


if __name__ == "__main__":
    main_script()
