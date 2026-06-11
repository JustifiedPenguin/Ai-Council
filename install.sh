#!/usr/bin/env bash

set -e

echo "== AI Council Installer =="

# Create venv
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

echo ""
echo "Installation complete!"
echo "Run the app with:"
echo "source venv/bin/activate && python council_ui.py"
