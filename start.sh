#!/bin/bash

# Keep this script LF-only; Linux must parse the shebang before Bash starts.

# CS2 Server Manager - Startup Script
# Starts the FastAPI API and/or the Next.js console.
# Modes: api | dev | build | start | build+start

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
FRONTEND="$ROOT/frontend"

PYTHON_BIN="${PYTHON_BIN:-}"
UV_CMD=()

usage() {
    cat <<'EOF'
CS2 Server Manager — start.sh

Usage:
  ./start.sh                 Interactive menu when a TTY is attached; otherwise api
  ./start.sh api             FastAPI / uvicorn on :8000
  ./start.sh dev             Next.js Turbopack dev server on :3000
  ./start.sh build           Next.js production build
  ./start.sh start           Serve the existing Next.js production build on :3000
  ./start.sh build+start     Next.js production build, then start
  ./start.sh --help          Show this help

Next.js reads INTERNAL_API_URL from frontend/.env (default http://127.0.0.1:8000).
The console is http://localhost:3000; FastAPI :8000 is the internal API.
EOF
}

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

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        UV_CMD=(uv)
        return 0
    fi

    install_uv

    if command -v uv >/dev/null 2>&1; then
        UV_CMD=(uv)
        return 0
    fi

    UV_CMD=("$PYTHON_BIN" -m uv)
    "${UV_CMD[@]}" --version >/dev/null 2>&1 || {
        echo "Error: uv was installed, but it is not available on PATH."
        exit 1
    }
}

ensure_frontend_env() {
    if [ ! -f "$FRONTEND/.env" ] && [ -f "$FRONTEND/.env.example" ]; then
        cp "$FRONTEND/.env.example" "$FRONTEND/.env"
        echo "Created frontend/.env from frontend/.env.example"
        echo "Edit INTERNAL_API_URL there if FastAPI is not at http://127.0.0.1:8000"
        echo ""
    fi
}

ensure_frontend_deps() {
    if [ ! -d "$FRONTEND/node_modules" ]; then
        echo "Installing frontend dependencies..."
        (cd "$FRONTEND" && npm ci)
        echo ""
    fi
}

run_frontend_npm() {
    ensure_frontend_env
    ensure_frontend_deps
    (cd "$FRONTEND" && npm run "$@")
}

start_api() {
    ensure_uv

    echo "=========================================="
    echo "CS2 Server Manager — API"
    echo "=========================================="
    echo ""
    echo "Starting FastAPI..."
    echo "Internal API: http://localhost:8000"
    echo "Use ./start.sh dev or ./start.sh build+start for the Next.js console."
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo "=========================================="
    echo ""

    export PYTHONUNBUFFERED=1
    "${UV_CMD[@]}" run --no-dev --python 3.14 --locked uvicorn main:app \
        --host 0.0.0.0 --port 8000 --workers 1 \
        --limit-concurrency 100 --backlog 2048 --timeout-keep-alive 5
}

start_next_dev() {
    echo "=========================================="
    echo "CS2 Server Manager — Next.js dev"
    echo "=========================================="
    echo ""
    echo "Console: http://localhost:3000"
    echo "Proxies /api to INTERNAL_API_URL from frontend/.env"
    echo "Press Ctrl+C to stop"
    echo "=========================================="
    echo ""
    run_frontend_npm dev
}

build_next() {
    echo "=========================================="
    echo "CS2 Server Manager — Next.js build"
    echo "=========================================="
    echo ""
    run_frontend_npm build
}

start_next() {
    echo "=========================================="
    echo "CS2 Server Manager — Next.js start"
    echo "=========================================="
    echo ""
    echo "Console: http://localhost:3000"
    echo "Requires a prior ./start.sh build (or npm run build)."
    echo "Press Ctrl+C to stop"
    echo "=========================================="
    echo ""
    run_frontend_npm start
}

build_and_start_next() {
    echo "=========================================="
    echo "CS2 Server Manager — Next.js build + start"
    echo "=========================================="
    echo ""
    echo "Console: http://localhost:3000"
    echo "Press Ctrl+C to stop after the build finishes"
    echo "=========================================="
    echo ""
    run_frontend_npm build:start
}

prompt_mode() {
    echo "==========================================" >&2
    echo "CS2 Server Manager — start" >&2
    echo "==========================================" >&2
    echo "" >&2
    echo "Select start mode / 请选择启动方式:" >&2
    echo "  1) api          FastAPI on :8000" >&2
    echo "  2) dev          Next.js dev on :3000" >&2
    echo "  3) build        Next.js production build" >&2
    echo "  4) start        Next.js production server on :3000" >&2
    echo "  5) build+start  Next.js build, then start" >&2
    echo "" >&2
    local choice=""
    read -r -p "Choice [1]: " choice || true
    case "${choice:-1}" in
        1|api) MODE=api ;;
        2|dev) MODE=dev ;;
        3|build) MODE=build ;;
        4|start) MODE=start ;;
        5|build+start|build-start) MODE=build+start ;;
        *)
            echo "Error: unknown choice '${choice}'" >&2
            exit 1
            ;;
    esac
}

assign_mode() {
    case "$1" in
        api|backend) MODE=api ;;
        dev|next-dev) MODE=dev ;;
        build|next-build) MODE=build ;;
        start|next-start) MODE=start ;;
        build+start|build-start|frontend) MODE=build+start ;;
        *)
            echo "Error: unknown mode '$1'" >&2
            echo "" >&2
            usage >&2
            exit 1
            ;;
    esac
}

MODE=""
if [ $# -eq 0 ]; then
    if [ -t 0 ]; then
        prompt_mode
    else
        MODE=api
    fi
else
    case "$1" in
        -h|--help|help)
            usage
            exit 0
            ;;
        *)
            assign_mode "$1"
            ;;
    esac
fi

case "$MODE" in
    api) start_api ;;
    dev) start_next_dev ;;
    build) build_next ;;
    start) start_next ;;
    build+start) build_and_start_next ;;
    *)
        echo "Error: unhandled mode '$MODE'" >&2
        exit 1
        ;;
esac
