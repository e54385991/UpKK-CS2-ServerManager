@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM CS2 Server Manager - Startup Script for Windows
REM This script installs uv when needed and starts the application with uv run.

echo ==========================================
echo CS2 Server Manager - Starting Application
echo ==========================================
echo.

set PYTHON_CMD=
py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3.14"
) else (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
    if %errorlevel%==0 set "PYTHON_CMD=python"
)

set "UV_CMD=uv"
!UV_CMD! --version >nul 2>&1
if errorlevel 1 (
    echo uv is not installed. Installing uv with pip...
    if "%PYTHON_CMD%"=="" (
        echo Error: uv is not installed and Python 3.14+ was not found to install it.
        echo Install Python 3.14+ first, then run: pip install uv
        exit /b 1
    )

    %PYTHON_CMD% -m ensurepip --upgrade >nul 2>&1
    %PYTHON_CMD% -m pip install --upgrade uv
    if errorlevel 1 (
        echo Error: Failed to install uv.
        exit /b 1
    )

    !UV_CMD! --version >nul 2>&1
    if errorlevel 1 (
        set "UV_CMD=%PYTHON_CMD% -m uv"
        !UV_CMD! --version >nul 2>&1
        if errorlevel 1 (
            echo Error: uv was installed, but it is not available on PATH.
            exit /b 1
        )
    )
)

echo.
echo Starting CS2 Server Manager...
echo Access the application at: http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo ==========================================
echo.

REM uv reads pyproject.toml/uv.lock, creates .venv if needed, and starts the app.
!UV_CMD! run --python 3.14 --locked uvicorn main:app --host 0.0.0.0 --port 8000 --reload
