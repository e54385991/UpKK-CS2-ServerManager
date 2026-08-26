#!/usr/bin/env python3
"""Validate the checked-in 1Panel local application package."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "deploy/1panel/apps/cs2-server-manager"
VERSION_ROOT = PACKAGE_ROOT / "1.0.0"
LOCALES = {
    "en",
    "es-es",
    "ja",
    "ms",
    "pt-br",
    "ru",
    "ko",
    "zh-Hant",
    "zh",
    "tr",
}
VARIABLE_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
IMAGE_PATTERN = re.compile(r"^[^\s:@]+:[^\s@]+@sha256:[0-9a-f]{64}$")
KNOWN_1PANEL_VARIABLES = {"CONTAINER_NAME"}


def fail(message: str) -> None:
    raise SystemExit(f"1Panel package validation failed: {message}")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        fail(f"cannot parse {path.relative_to(PROJECT_ROOT)}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(PROJECT_ROOT)} must contain a YAML mapping")
    return value


def form_env_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        env_key = value.get("envKey")
        if isinstance(env_key, str):
            keys.add(env_key)
        keys.update(form_env_keys(value.get("child")))
    elif isinstance(value, list):
        for item in value:
            keys.update(form_env_keys(item))
    return keys


def validate_form_labels(value: Any, location: str = "formFields") -> None:
    if isinstance(value, dict):
        label = value.get("label")
        if label is not None:
            if not isinstance(label, dict) or set(label) != LOCALES:
                fail(f"{location}.label must contain exactly the appstore locales")
        if "child" in value:
            validate_form_labels(value["child"], f"{location}.child")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_form_labels(item, f"{location}[{index}]")


def run_compose_config(compose_path: Path, variables: dict[str, str]) -> None:
    docker = shutil.which("docker")
    if docker is None:
        print("WARN: docker is unavailable; skipped 1Panel docker compose config")
        return
    version = subprocess.run(
        [docker, "compose", "version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode != 0:
        print("WARN: docker compose plugin is unavailable; skipped compose config")
        return
    environment = os.environ.copy()
    environment.update(variables)
    result = subprocess.run(
        [docker, "compose", "-f", str(compose_path), "config", "--quiet"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"docker compose config failed: {result.stderr.strip()}")


def main() -> None:
    required_files = (
        PACKAGE_ROOT / "logo.png",
        PACKAGE_ROOT / "README.md",
        PACKAGE_ROOT / "README_en.md",
        PACKAGE_ROOT / "data.yml",
        VERSION_ROOT / "data.yml",
        VERSION_ROOT / "docker-compose.yml",
        VERSION_ROOT / "data/.gitkeep",
        VERSION_ROOT / "scripts/init.sh",
    )
    for path in required_files:
        if not path.is_file():
            fail(f"missing {path.relative_to(PROJECT_ROOT)}")

    root_data = load_yaml(PACKAGE_ROOT / "data.yml")
    metadata = root_data.get("additionalProperties")
    if not isinstance(metadata, dict):
        fail("root additionalProperties is required")
    for key in (
        "key",
        "name",
        "tags",
        "shortDescZh",
        "shortDescEn",
        "description",
        "type",
        "crossVersionUpdate",
        "limit",
        "architectures",
    ):
        if key not in metadata:
            fail(f"root metadata is missing {key}")
    descriptions = metadata["description"]
    if not isinstance(descriptions, dict) or set(descriptions) != LOCALES:
        fail("root metadata.description must contain exactly the appstore locales")
    if metadata["key"] != PACKAGE_ROOT.name:
        fail("root metadata.key must match the package directory")

    version_data = load_yaml(VERSION_ROOT / "data.yml")
    version_fields = version_data.get("additionalProperties", {}).get("formFields")
    if not isinstance(version_fields, list) or not version_fields:
        fail("version additionalProperties.formFields is required")
    validate_form_labels(version_fields)
    declared_variables = form_env_keys(version_fields)

    compose = load_yaml(VERSION_ROOT / "docker-compose.yml")
    services = compose.get("services")
    if not isinstance(services, dict) or set(services) != {"app"}:
        fail("the package must contain only the app service")
    app = services["app"]
    if not isinstance(app, dict):
        fail("app service must be a mapping")
    if app.get("container_name") != "${CONTAINER_NAME}":
        fail("app must use container_name: ${CONTAINER_NAME}")
    if app.get("restart") != "always":
        fail("app must use restart: always")
    if app.get("labels", {}).get("createdBy") != "Apps":
        fail("app must have labels.createdBy: Apps")
    networks = compose.get("networks", {})
    if networks.get("1panel-network", {}).get("external") is not True:
        fail("1panel-network must be external")
    if "1panel-network" not in app.get("networks", []):
        fail("app must join 1panel-network")
    if "./data:/app/data" not in app.get("volumes", []):
        fail("app must persist ./data:/app/data")
    image = app.get("image")
    if not isinstance(image, str) or IMAGE_PATTERN.fullmatch(image) is None:
        fail("app image must use a tag and immutable sha256 digest")
    variables = set(VARIABLE_PATTERN.findall((VERSION_ROOT / "docker-compose.yml").read_text()))
    undeclared = variables - declared_variables - KNOWN_1PANEL_VARIABLES
    if undeclared:
        fail(f"compose variables are missing from formFields: {sorted(undeclared)}")
    for volume in app.get("volumes", []):
        source = str(volume).split(":", 1)[0]
        if source.startswith("/") or "docker.sock" in source:
            fail(f"dangerous host volume is not allowed: {volume}")
    if not (VERSION_ROOT / "scripts/init.sh").stat().st_mode & 0o111:
        fail("scripts/init.sh must be executable")
    redis_field = next(
        (field for field in version_fields if field.get("envKey") == "PANEL_REDIS_DB"), None
    )
    if (
        not isinstance(redis_field, dict)
        or redis_field.get("type") != "number"
        or redis_field.get("rule") != "integerNumberWith0"
    ):
        fail("PANEL_REDIS_DB must be a non-negative integer field")
    backend_field = next(
        (field for field in version_fields if field.get("envKey") == "BACKEND_URL"), None
    )
    if not isinstance(backend_field, dict) or backend_field.get("rule") != "paramExtUrl":
        fail("BACKEND_URL must use the 1Panel URL validator")
    if backend_field.get("default") != "http://0.0.0.0:8000":
        fail("BACKEND_URL must default to http://0.0.0.0:8000")
    port_field = next(
        (field for field in version_fields if field.get("envKey") == "PANEL_APP_PORT_HTTP"), None
    )
    if (
        not isinstance(port_field, dict)
        or port_field.get("type") != "number"
        or port_field.get("rule") != "paramPort"
        or port_field.get("edit") is not True
    ):
        fail("PANEL_APP_PORT_HTTP must be an editable external port field")
    if "${PANEL_APP_PORT_HTTP}:8000" not in (VERSION_ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    ):
        fail("compose must map PANEL_APP_PORT_HTTP to the container port 8000")
    for secret_key in ("SECRET_KEY", "JWT_SECRET_KEY"):
        secret_field = next(
            (field for field in version_fields if field.get("envKey") == secret_key), None
        )
        if not isinstance(secret_field, dict) or secret_field.get("random") is not True:
            fail(f"{secret_key} must be generated by 1Panel and hardened by init.sh")
    init_script = (VERSION_ROOT / "scripts/init.sh").read_text(encoding="utf-8")
    if "openssl rand -hex 32" not in init_script or "ensure_secret SECRET_KEY" not in init_script:
        fail("init.sh must upgrade generated secrets to 256-bit values")
    for database_field in ("PANEL_DB_NAME", "PANEL_DB_USER", "PANEL_DB_USER_PASSWORD"):
        field = next(
            (item for item in version_fields if item.get("envKey") == database_field), None
        )
        if not isinstance(field, dict) or field.get("random") is not True:
            fail(f"{database_field} must be generated automatically by 1Panel")

    run_compose_config(
        VERSION_ROOT / "docker-compose.yml",
        {
            "CONTAINER_NAME": "cs2-manager-package-check",
            "PANEL_DB_TYPE": "postgresql",
            "PANEL_DB_HOST": "postgresql",
            "PANEL_DB_USER": "cs2_manager",
            "PANEL_DB_USER_PASSWORD": "test-password",
            "PANEL_DB_NAME": "cs2_manager",
            "PANEL_REDIS_TYPE": "redis",
            "PANEL_REDIS_HOST": "redis",
            "PANEL_REDIS_PASSWORD": "",
            "PANEL_REDIS_DB": "0",
            "PANEL_APP_PORT_HTTP": "18000",
            "BACKEND_URL": "http://localhost:18000",
            "SECRET_KEY": "test-secret-key",
            "JWT_SECRET_KEY": "test-jwt-secret-key",
            "GOOGLE_CLIENT_ID": "",
        },
    )
    print("1Panel application package validation passed.")


if __name__ == "__main__":
    main()
