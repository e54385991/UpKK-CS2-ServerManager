@echo off
REM CS2 Server Manager - Startup Script for Windows
REM This script starts the CS2 Server Manager application

echo ==========================================
echo CS2 Server Manager - Starting Application
echo ==========================================
echo.

REM Check if virtual environment exists
if exist venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Install/Update dependencies
echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Starting CS2 Server Manager...
echo Access the application at: http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo ==========================================
echo.

REM Start the application
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
