#!/bin/bash
set -e

uv venv -p 3.13 --allow-existing
echo "Installing jupyter dependencies..."
uv pip install jupyter ipykernel maturin

echo "Starting Jupyter Lab..."
uv run jupyter lab
