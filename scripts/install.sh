#!/usr/bin/env bash

set -e

echo "== AI Council Installer =="

if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is required but not installed."
    exit 1
fi

python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Installation complete!"
echo "Run with:"
echo "./run.sh (recommended)"
echo "or:"
echo "source venv/bin/activate && python council.py"
