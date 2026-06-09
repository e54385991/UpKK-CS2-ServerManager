@echo off
setlocal
REM CS2 Server Manager - Startup Script for Windows
REM This script starts the CS2 Server Manager application

echo ==========================================
echo CS2 Server Manager - Starting Application
echo ==========================================
echo.

set PYTHON_CMD=
py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_CMD=py -3.14
) else (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
    if %errorlevel%==0 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
    echo Error: Python 3.14 or newer is required.
    exit /b 1
)

if not exist venv\Scripts\activate.bat (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment.
        exit /b 1
    )
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Error: Existing venv is not using Python 3.14+.
    echo Remove the venv directory and run this script again.
    exit /b 1
)

echo Upgrading pip...
python -m pip install --upgrade pip

REM Install/Update dependencies
echo Installing dependencies...
python -m pip install -r requirements.txt

echo.
echo Starting CS2 Server Manager...
echo Access the application at: http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo ==========================================
echo.

REM Start the application
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
