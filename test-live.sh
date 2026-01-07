#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create venv in python dir
cd "$SCRIPT_DIR/python"
uv venv --allow-existing
source .venv/bin/activate

# Build cleon (includes PyO3 bindings via maturin)
echo "Building cleon..."
uv pip install maturin
maturin develop

# Install test dependencies
uv pip install pytest

echo "Running live tests..."
PIMONO_LIVE_TEST=1 pytest tests/test_pimono_backend.py -v -k "Live or EventStreaming or SessionResume"
