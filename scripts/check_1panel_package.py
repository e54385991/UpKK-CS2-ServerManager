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
IMAGE_PATTERN = re.compile(r"^[^\s:@]+:[^\s@]+(?:@sha256:[0-9a-f]{64})?$")
SIMPLE_VARIABLE_PATTERN = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
KNOWN_1PANEL_VARIABLES = {"CONTAINER_NAME"}
REQUIRED_SERVICES = {"app", "frontend", "caddy"}
DEFAULT_INTERNAL_API_URL = "http://app:8000"
PRIVATE_NETWORK = "cs2"
DEFAULT_APP_IMAGE = "docker.io/e54385991/upkk-cs2-server-manager:latest"
DEFAULT_WEB_IMAGE = "docker.io/e54385991/upkk-cs2-server-manager-web:latest"
DEFAULT_PUBLIC_CONSOLE_PORT = 31800


def form_field_default(fields: list[Any], env_key: str) -> str:
    field = next((item for item in fields if item.get("envKey") == env_key), None)
    if not isinstance(field, dict):
        return ""
    value = field.get("default")
    return value if isinstance(value, str) else ""


def compose_image_ref(raw: object, fields: list[Any]) -> str:
    if not isinstance(raw, str):
        return ""
    # 1Panel pulls `image:` before Compose interpolation. `${VAR:-default}` is
    # treated as a literal ref and fails with "invalid reference format".
    if ":-" in raw:
        fail("1Panel image refs must be ${VAR} or a literal; ${VAR:-default} cannot be pulled")
    match = SIMPLE_VARIABLE_PATTERN.fullmatch(raw)
    if match:
        return form_field_default(fields, match.group(1))
    return raw


def require_1panel_service(
    name: str, service: dict[str, Any], *, panel_network: bool = True
) -> None:
    if service.get("restart") != "always":
        fail(f"{name} must use restart: always")
    labels = service.get("labels")
    if not isinstance(labels, dict) or labels.get("createdBy") != "Apps":
        fail(f"{name} must have labels.createdBy: Apps")
    networks = service.get("networks", [])
    if not isinstance(networks, list):
        fail(f"{name} networks must be a list")
    if PRIVATE_NETWORK not in networks:
        fail(f"{name} must join the private {PRIVATE_NETWORK} network")
    if panel_network:
        if "1panel-network" not in networks:
            fail(f"{name} must join 1panel-network to reach PostgreSQL and Redis")
    elif "1panel-network" in networks:
        fail(
            f"{name} must not join 1panel-network; service name {name} collides across two installs"
        )
    for volume in service.get("volumes", []):
        source = str(volume).split(":", 1)[0]
        if source.startswith("/") or "docker.sock" in source:
            fail(f"dangerous host volume is not allowed: {volume}")


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


def _validate_package_files_and_metadata() -> None:
    required_files = (
        PACKAGE_ROOT / "logo.png",
        PACKAGE_ROOT / "README.md",
        PACKAGE_ROOT / "README_en.md",
        PACKAGE_ROOT / "data.yml",
        VERSION_ROOT / "data.yml",
        VERSION_ROOT / "docker-compose.yml",
        VERSION_ROOT / "Caddyfile",
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


def _load_version_fields() -> list[Any]:
    version_data = load_yaml(VERSION_ROOT / "data.yml")
    version_fields = version_data.get("additionalProperties", {}).get("formFields")
    if not isinstance(version_fields, list) or not version_fields:
        fail("version additionalProperties.formFields is required")
    validate_form_labels(version_fields)
    return version_fields


def _compose_services() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    compose = load_yaml(VERSION_ROOT / "docker-compose.yml")
    services = compose.get("services")
    if not isinstance(services, dict) or set(services) != REQUIRED_SERVICES:
        fail("the package must contain app, frontend, and caddy services")
    app = services["app"]
    frontend = services["frontend"]
    caddy = services["caddy"]
    if not isinstance(app, dict):
        fail("app service must be a mapping")
    if not isinstance(frontend, dict):
        fail("frontend service must be a mapping")
    if not isinstance(caddy, dict):
        fail("caddy service must be a mapping")
    require_1panel_service("app", app, panel_network=True)
    require_1panel_service("frontend", frontend, panel_network=False)
    require_1panel_service("caddy", caddy, panel_network=False)
    if app.get("container_name") != "${CONTAINER_NAME}":
        fail("app must use container_name: ${CONTAINER_NAME}")
    if frontend.get("container_name") != "${CONTAINER_NAME}-web":
        fail("frontend must use container_name: ${CONTAINER_NAME}-web")
    if caddy.get("container_name") != "${CONTAINER_NAME}-edge":
        fail("caddy must use container_name: ${CONTAINER_NAME}-edge")
    return compose, app, frontend, caddy


def _validate_compose_runtime(
    compose: dict[str, Any],
    app: dict[str, Any],
    frontend: dict[str, Any],
    version_fields: list[Any],
) -> None:
    networks = compose.get("networks", {})
    if networks.get("1panel-network", {}).get("external") is not True:
        fail("1panel-network must be external")
    private_network = networks.get(PRIVATE_NETWORK)
    if PRIVATE_NETWORK not in networks or (
        isinstance(private_network, dict) and private_network.get("external")
    ):
        fail("compose must define a private non-external cs2 network")
    if "./data:/app/data" not in app.get("volumes", []):
        fail("app must persist ./data:/app/data")
    if app.get("ports"):
        fail("app must not publish a host port; Caddy is the public root")
    if "8000" not in [str(item) for item in app.get("expose", [])]:
        fail("app must expose the private API port 8000")
    image = compose_image_ref(app.get("image"), version_fields)
    if IMAGE_PATTERN.fullmatch(image) is None:
        fail("app image must use a tagged Docker Hub image")
    frontend_image = compose_image_ref(frontend.get("image"), version_fields)
    if IMAGE_PATTERN.fullmatch(frontend_image) is None:
        fail("frontend image must use a tagged Next.js console image")
    if form_field_default(version_fields, "CS2_MANAGER_IMAGE") != DEFAULT_APP_IMAGE:
        fail("CS2_MANAGER_IMAGE must default to the pinned backend image")
    if form_field_default(version_fields, "CS2_FRONTEND_IMAGE") != DEFAULT_WEB_IMAGE:
        fail("CS2_FRONTEND_IMAGE must default to the published frontend image")


def _validate_compose_environment(app: dict[str, Any], frontend: dict[str, Any]) -> dict[str, Any]:
    frontend_env = frontend.get("environment")
    if not isinstance(frontend_env, dict):
        fail("frontend environment is required")
    internal_api = frontend_env.get("INTERNAL_API_URL")
    if internal_api != DEFAULT_INTERNAL_API_URL:
        fail("frontend must proxy API calls to app:8000 on the private cs2 network")
    if frontend_env.get("SESSION_COOKIE_SUFFIX") != "${PANEL_APP_PORT_HTTP}":
        fail("frontend must suffix the session cookie with PANEL_APP_PORT_HTTP")
    if frontend_env.get("PUBLIC_APP_URL"):
        fail("frontend must not pin PUBLIC_APP_URL; derive the browser origin from Host")
    if frontend.get("extra_hosts"):
        fail(
            "1Panel compose must not set extra_hosts; host-gateway fails on "
            "some 1Panel Docker engines; Next talks to app:8000 on the private network"
        )
    app_env = app.get("environment")
    if not isinstance(app_env, dict) or app_env.get("CONSOLE_PUBLIC_URL") != "${BACKEND_URL}":
        fail("app must set CONSOLE_PUBLIC_URL from BACKEND_URL")
    if str(app_env.get("API_PORT")) != "8000":
        fail("FastAPI must stay on container port 8000; 8001 is not a second-instance port")
    if app_env.get("REDIS_KEY_PREFIX") != "${CONTAINER_NAME}":
        fail("app must prefix Redis keys with CONTAINER_NAME")
    if app_env.get("SESSION_COOKIE_SUFFIX") != "${PANEL_APP_PORT_HTTP}":
        fail("app must suffix the session cookie with the console HTTP port")
    if app_env.get("SESSION_COOKIE_SUFFIX") in {"8000", "8001", "${API_PORT}"}:
        fail("session cookie suffix must follow PANEL_APP_PORT_HTTP, not FastAPI")
    return app_env


def _validate_compose_edge(
    compose: dict[str, Any], app: dict[str, Any], caddy: dict[str, Any], version_fields: list[Any]
) -> str:
    declared_variables = form_env_keys(version_fields)
    caddy_image = caddy.get("image")
    if not isinstance(caddy_image, str) or IMAGE_PATTERN.fullmatch(caddy_image) is None:
        fail("caddy image must use a tag and immutable sha256 digest")
    caddyfile = (VERSION_ROOT / "Caddyfile").read_text(encoding="utf-8")
    if "reverse_proxy frontend:3000" not in caddyfile:
        fail("1Panel Caddyfile must reverse-proxy frontend:3000 on the private network")
    if "reverse_proxy {$FRONTEND_UPSTREAM}" in caddyfile:
        fail("1Panel Caddyfile must not use a shared-network FRONTEND_UPSTREAM")
    if "reverse_proxy app:8000" in caddyfile:
        fail("1Panel Caddyfile must not expose FastAPI as the public root")
    if caddy.get("environment"):
        fail("caddy must not take FRONTEND_UPSTREAM from the shared 1panel-network")
    if "./Caddyfile:/etc/caddy/Caddyfile:ro" not in caddy.get("volumes", []):
        fail("caddy must mount the package Caddyfile read-only")
    compose_text = (VERSION_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    variables = set(VARIABLE_PATTERN.findall(compose_text))
    undeclared = variables - declared_variables - KNOWN_1PANEL_VARIABLES
    if undeclared:
        fail(f"compose variables are missing from formFields: {sorted(undeclared)}")
    if not (VERSION_ROOT / "scripts/init.sh").stat().st_mode & 0o111:
        fail("scripts/init.sh must be executable")
    return compose_text


def _validate_compose(version_fields: list[Any]) -> tuple[str, dict[str, Any]]:
    compose, app, frontend, caddy = _compose_services()
    _validate_compose_runtime(compose, app, frontend, version_fields)
    app_env = _validate_compose_environment(app, frontend)
    compose_text = _validate_compose_edge(compose, app, caddy, version_fields)
    return compose_text, app_env


def _require_random_fields(version_fields: list[Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        field = next((item for item in version_fields if item.get("envKey") == key), None)
        if not isinstance(field, dict) or field.get("random") is not True:
            fail(f"{key} must be generated automatically by 1Panel")


def _validate_form_and_init(
    version_fields: list[Any], compose_text: str, app_env: dict[str, Any]
) -> None:
    redis_field = next(
        (field for field in version_fields if field.get("envKey") == "PANEL_REDIS_DB"), None
    )
    if (
        not isinstance(redis_field, dict)
        or redis_field.get("type") != "number"
        or redis_field.get("rule") != "integerNumberWith0"
    ):
        fail("PANEL_REDIS_DB must be a non-negative integer field")
    redis_password_field = next(
        (field for field in version_fields if field.get("envKey") == "PANEL_REDIS_ROOT_PASSWORD"),
        None,
    )
    if (
        not isinstance(redis_password_field, dict)
        or redis_password_field.get("type") != "password"
        or redis_password_field.get("required") is not True
        or redis_password_field.get("random") is True
    ):
        fail("PANEL_REDIS_ROOT_PASSWORD must be a required password field")
    if app_env.get("REDIS_PASSWORD") != "${PANEL_REDIS_ROOT_PASSWORD}":
        fail("app must take REDIS_PASSWORD from PANEL_REDIS_ROOT_PASSWORD")
    backend_field = next(
        (field for field in version_fields if field.get("envKey") == "BACKEND_URL"), None
    )
    if not isinstance(backend_field, dict) or backend_field.get("rule") != "paramExtUrl":
        fail("BACKEND_URL must use the 1Panel URL validator")
    if backend_field.get("default") != f"http://localhost:{DEFAULT_PUBLIC_CONSOLE_PORT}":
        fail(
            "BACKEND_URL must default to the browser origin "
            f"http://localhost:{DEFAULT_PUBLIC_CONSOLE_PORT}"
        )
    if "0.0.0.0" in str(backend_field.get("default")):
        fail("BACKEND_URL must not default to a bind address")
    internal_api_field = next(
        (field for field in version_fields if field.get("envKey") == "FRONTEND_INTERNAL_API_URL"),
        None,
    )
    if (
        not isinstance(internal_api_field, dict)
        or internal_api_field.get("default") != DEFAULT_INTERNAL_API_URL
        or internal_api_field.get("edit") is not False
    ):
        fail("FRONTEND_INTERNAL_API_URL must be locked to http://app:8000")
    port_field = next(
        (field for field in version_fields if field.get("envKey") == "PANEL_APP_PORT_HTTP"), None
    )
    if (
        not isinstance(port_field, dict)
        or port_field.get("type") != "number"
        or port_field.get("rule") != "paramPort"
        or port_field.get("edit") is not True
        or port_field.get("default") != DEFAULT_PUBLIC_CONSOLE_PORT
    ):
        fail(
            "PANEL_APP_PORT_HTTP must be an editable console port "
            f"defaulting to {DEFAULT_PUBLIC_CONSOLE_PORT}"
        )
    if "${PANEL_APP_PORT_HTTP}:80" not in compose_text:
        fail("compose must map PANEL_APP_PORT_HTTP to Caddy port 80")
    if "${PANEL_APP_PORT_HTTP}:8000" in compose_text:
        fail("compose must not map the public HTTP port onto FastAPI :8000")
    if ":8001" in compose_text or "8001:" in compose_text:
        fail("compose must not publish or remap FastAPI as 8001")
    _require_random_fields(version_fields, ("SECRET_KEY", "JWT_SECRET_KEY"))
    init_script = (VERSION_ROOT / "scripts/init.sh").read_text(encoding="utf-8")
    if "openssl rand -hex 32" not in init_script or "ensure_secret SECRET_KEY" not in init_script:
        fail("init.sh must upgrade generated secrets to 256-bit values")
    _require_random_fields(
        version_fields, ("PANEL_DB_NAME", "PANEL_DB_USER", "PANEL_DB_USER_PASSWORD")
    )


def _run_package_compose() -> None:
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
            "PANEL_REDIS_ROOT_PASSWORD": "test-redis-password",
            "PANEL_REDIS_DB": "0",
            "PANEL_APP_PORT_HTTP": "18000",
            "BACKEND_URL": "http://localhost:18000",
            "FRONTEND_INTERNAL_API_URL": "http://app:8000",
            "CS2_MANAGER_IMAGE": DEFAULT_APP_IMAGE,
            "CS2_FRONTEND_IMAGE": DEFAULT_WEB_IMAGE,
            "SECRET_KEY": "test-secret-key",
            "JWT_SECRET_KEY": "test-jwt-secret-key",
            "GOOGLE_CLIENT_ID": "",
        },
    )
    print("1Panel application package validation passed.")


def main() -> None:
    _validate_package_files_and_metadata()
    version_fields = _load_version_fields()
    compose_text, app_env = _validate_compose(version_fields)
    _validate_form_and_init(version_fields, compose_text, app_env)
    _run_package_compose()
    print("1Panel application package validation passed.")


if __name__ == "__main__":
    main()
