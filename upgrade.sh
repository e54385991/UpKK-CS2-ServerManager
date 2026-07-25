#!/bin/bash

# CS2 Server Manager - source upgrade helper
# Prepares dependencies and configuration, then upgrades the database.

set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-}"

find_bootstrap_python() {
    if [ -n "$PYTHON_BIN" ]; then
        return 0
    fi

    if command -v python3.14 >/dev/null 2>&1; then
        PYTHON_BIN="python3.14"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    fi
}

install_uv() {
    find_bootstrap_python

    if [ -z "$PYTHON_BIN" ]; then
        echo "Error: uv is not installed and Python 3.14+ was not found to install it." >&2
        exit 1
    fi

    if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)'; then
        echo "Error: uv is not installed and Python 3.14+ is required to install it." >&2
        echo "Current interpreter: $("$PYTHON_BIN" --version 2>&1)" >&2
        exit 1
    fi

    echo "uv is not installed. Installing uv with pip..."
    "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$PYTHON_BIN" -m pip install --upgrade uv
}

if command -v uv >/dev/null 2>&1; then
    UV_CMD=(uv)
else
    install_uv
    if command -v uv >/dev/null 2>&1; then
        UV_CMD=(uv)
    else
        UV_CMD=("$PYTHON_BIN" -m uv)
        "${UV_CMD[@]}" --version >/dev/null 2>&1 || {
            echo "Error: uv was installed, but it is not available on PATH." >&2
            exit 1
        }
    fi
fi

echo "Preparing locked Python 3.14 dependencies..."
"${UV_CMD[@]}" sync --python 3.14 --locked

echo "Preparing .env without overwriting operator values..."
"${UV_CMD[@]}" run --locked python scripts/prepare_env.py \
    --env-file "$PROJECT_ROOT/.env" \
    --example-file "$PROJECT_ROOT/.env.example"

echo "Upgrading the database under the migration advisory lock..."
"${UV_CMD[@]}" run --locked python -m cs2_manager.migrate upgrade

echo "Verifying the database revision..."
"${UV_CMD[@]}" run --locked python -m cs2_manager.migrate check

echo "Upgrade complete. Start the single-worker source service with ./start.sh."
