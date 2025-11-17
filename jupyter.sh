#!/bin/bash
set -e

export UV_VENV_CLEAR=1
uv venv -p 3.13
echo "Installing jupyter dependencies..."
uv pip install jupyter ipykernel maturin

echo "Starting Jupyter Lab..."
uv run jupyter lab
