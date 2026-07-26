#!/bin/bash

# Keep this script LF-only; Linux must parse the shebang before Bash starts.

# CS2 Server Manager - Startup Script
# This script installs uv when needed and starts the application with uv run.

set -e

echo "=========================================="
echo "CS2 Server Manager - Starting Application"
echo "=========================================="
echo ""

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
        echo "Error: uv is not installed and Python 3.14+ was not found to install it."
        echo "Install Python 3.14+ first, then run: pip install uv"
        exit 1
    fi

    if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)'; then
        echo "Error: uv is not installed and Python 3.14+ is required to install it."
        echo "Current interpreter: $($PYTHON_BIN --version 2>&1)"
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
            echo "Error: uv was installed, but it is not available on PATH."
            exit 1
        }
    fi
fi

echo ""
echo "Starting CS2 Server Manager..."
echo "Access the application at: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo "=========================================="
echo ""

# uv reads pyproject.toml/uv.lock, creates .venv if needed, and starts the app.
"${UV_CMD[@]}" run --python 3.14 --locked uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --limit-concurrency 100 --backlog 2048 --timeout-keep-alive 5
