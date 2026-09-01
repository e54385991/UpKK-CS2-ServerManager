#!/usr/bin/env python3
"""Check the versioned HTTP contract for explicit, safe response models.

The legacy API is intentionally left on its compatibility surface.  The
versioned API is the maintained boundary used by the Next.js console, so every
JSON route must opt into a response model (or explicitly opt out for a stream
or file response).  The runtime check also catches accidental reintroduction
of secret fields into response schemas.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from fastapi.routing import APIRoute, iter_route_contexts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V1_ROUTES = PROJECT_ROOT / "api" / "routes" / "v1"
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "ssh_password",
        "sudo_password",
        "rcon_password",
        "steam_account_token",
        "discord_webhook_url",
        "secret_access_key",
        "smtp_password",
        "gmail_credentials_json",
        "gmail_token_json",
        "global_github_token",
        "github_token",
        "steam_api_key",
        "api_key",
        "access_token",
        "login_token",
    }
)

# These are deliberately narrow, one-time or explicitly requested capabilities.
SECRET_RESPONSE_ALLOWLIST: dict[str, frozenset[str]] = {
    "/api/v1/auth/google-oauth": frozenset({"access_token"}),
    "/api/v1/profile/api-key": frozenset({"api_key"}),
    "/api/v1/profile/gslt": frozenset({"login_token"}),
    "/api/v1/setup/initialized-servers/{server_key:path}/credentials": frozenset({"ssh_password"}),
    "/api/v1/setup/manual-script": frozenset({"password"}),
    "/api/v1/server-configs": SENSITIVE_FIELDS,
}


def _route_decorator_has_response_model(tree: ast.AST) -> list[str]:
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not any(keyword.arg == "response_model" for keyword in decorator.keywords):
                missing.append(f"{node.name}:{node.lineno}")
    return missing


def _schema_fields(schema: object) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    fields = set(schema.get("properties", {}))
    for value in schema.get("properties", {}).values():
        fields.update(_schema_fields(value))
    for value in schema.get("$defs", {}).values():
        fields.update(_schema_fields(value))
    for value in schema.get("items", {}).values() if isinstance(schema.get("items"), dict) else ():
        fields.update(_schema_fields(value))
    return fields


def check() -> list[str]:
    violations: list[str] = []
    for path in sorted(V1_ROUTES.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for item in _route_decorator_has_response_model(tree):
            violations.append(f"{path.relative_to(PROJECT_ROOT)}:{item}: missing response_model")

    # Importing the app is intentional: this validates the actual composed
    # contract, including routers nested through APIRouter.include_router().
    sys.path.insert(0, str(PROJECT_ROOT))
    from api.application import create_app

    for context in iter_route_contexts(create_app(lifespan=None).routes):
        route = context.route
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        if route.response_model is None:
            continue
        schema = (
            route.response_model.model_json_schema()
            if hasattr(route.response_model, "model_json_schema")
            else {}
        )
        leaked = _schema_fields(schema) & SENSITIVE_FIELDS
        leaked -= SECRET_RESPONSE_ALLOWLIST.get(route.path, frozenset())
        if leaked:
            violations.append(f"{route.path}: sensitive response fields: {sorted(leaked)}")
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("Versioned API contract violations:")
        print("\n".join(f"  - {item}" for item in violations))
        return 1
    print("Versioned API contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
