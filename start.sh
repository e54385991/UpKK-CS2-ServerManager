#!/bin/bash

# CS2 Server Manager - Startup Script
# This script starts the CS2 Server Manager application

set -e

echo "=========================================="
echo "CS2 Server Manager - Starting Application"
echo "=========================================="
echo ""

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3.14 >/dev/null 2>&1; then
        PYTHON_BIN="python3.14"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    else
        echo "Error: Python 3.14 or newer is required"
        exit 1
    fi
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)'; then
    echo "Error: Python 3.14 or newer is required"
    echo "Current interpreter: $($PYTHON_BIN --version 2>&1)"
    exit 1
fi

# Check if virtual environment exists, create if not
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_BIN" -m venv venv
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment"
        echo "Please ensure the Python 3.14 venv package is installed."
        exit 1
    fi
fi

echo "Activating virtual environment..."
source venv/bin/activate

if ! python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)'; then
    echo "Error: Existing venv is not using Python 3.14+"
    echo "Current venv interpreter: $(python --version 2>&1)"
    echo "Remove the venv directory and run this script again."
    exit 1
fi

echo "Upgrading pip..."
python -m pip install --upgrade pip

# Install/Update dependencies
echo "Installing dependencies..."
python -m pip install -r requirements.txt

echo ""
echo "Starting CS2 Server Manager..."
echo "Access the application at: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo "=========================================="
echo ""

# Start the application
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
