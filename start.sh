#!/bin/bash

# CS2 Server Manager - Startup Script
# This script starts the CS2 Server Manager application


echo "=========================================="
echo "CS2 Server Manager - Starting Application x"
echo "=========================================="
echo ""
sudo apt-get install python3-venv -y

# Check if virtual environment exists, create if not
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment"
        echo "Please ensure python3-venv is installed: sudo apt-get install python3-venv"
        exit 1
    fi
    
    # Activate virtual environment
    echo "Activating virtual environment..."
    source venv/bin/activate
    
    # Upgrade pip in the virtual environment
    echo "Upgrading pip..."
    python -m pip install --upgrade pip
else
    # Activate existing virtual environment
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Install/Update dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "Starting CS2 Server Manager..."
echo "Access the application at: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop the server"
echo "=========================================="
echo ""

# Start the application
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
