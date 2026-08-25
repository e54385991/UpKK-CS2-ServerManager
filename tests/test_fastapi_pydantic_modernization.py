"""Source-level guards for the FastAPI and Pydantic v2 conventions."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
PRODUCTION_PATHS = (
    PROJECT_ROOT / "api",
    PROJECT_ROOT / "modules",
    PROJECT_ROOT / "services",
    PROJECT_ROOT / "main.py",
)
DEPRECATED_MODEL_METHODS = {
    "construct",
    "dict",
    "from_orm",
    "parse_file",
    "parse_obj",
    "parse_raw",
    "schema",
    "schema_json",
}


def _production_files():
    for path in PRODUCTION_PATHS:
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _location(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(PROJECT_ROOT)}:{getattr(node, 'lineno', '?')}"


def test_fastapi_dependencies_use_annotated_in_production_code():
    violations = []

    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            defaults = [*node.args.defaults, *(item for item in node.args.kw_defaults if item)]
            for default in defaults:
                if isinstance(default, ast.Call) and _call_name(default.func) == "Depends":
                    violations.append(_location(path, default))

    assert not violations, (
        "Use Annotated dependency types instead of '= Depends(...)':\n" + "\n".join(violations)
    )


def test_production_code_uses_only_pydantic_v2_apis():
    violations = []

    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pydantic.v1"):
                violations.append(f"{_location(path, node)} imports pydantic.v1")
            elif isinstance(node, ast.Import) and any(
                alias.name.startswith("pydantic.v1") for alias in node.names
            ):
                violations.append(f"{_location(path, node)} imports pydantic.v1")
            elif isinstance(node, ast.ClassDef) and node.name == "Config":
                violations.append(f"{_location(path, node)} declares legacy class Config")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in DEPRECATED_MODEL_METHODS:
                    violations.append(
                        f"{_location(path, node)} calls deprecated .{node.func.attr}()"
                    )
            elif isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Dict) and any(
                    isinstance(target, ast.Name) and target.id == "model_config"
                    for target in node.targets
                ):
                    violations.append(f"{_location(path, node)} assigns model_config as a dict")
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "model_config"
                and isinstance(node.value, ast.Dict)
            ):
                violations.append(f"{_location(path, node)} assigns model_config as a dict")

            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    decorator_name = _call_name(
                        decorator.func if isinstance(decorator, ast.Call) else decorator
                    )
                    if decorator_name in {"validator", "root_validator"}:
                        violations.append(
                            f"{_location(path, decorator)} uses legacy @{decorator_name}"
                        )

    assert not violations, "Pydantic v1 or deprecated API usage found:\n" + "\n".join(violations)
