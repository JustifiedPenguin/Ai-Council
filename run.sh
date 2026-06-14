#!/usr/bin/env bash

set -e

echo "== AI Council Launcher =="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Python3 is required but not installed."
    exit 1
fi

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "No virtual environment found. Creating one..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Check if dependencies are installed
if [ ! -f "venv/.installed" ]; then
    echo "Installing dependencies..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt

    # Mark as installed
    touch venv/.installed
else
    echo "Dependencies already installed"
fi

# Run the app
echo "Launching AI Council..."
python council.py
