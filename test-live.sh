#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create venv in python dir
cd "$SCRIPT_DIR/python"
uv venv --allow-existing
source .venv/bin/activate

# Build pi_mono from pi-mono-rust
echo "Building pi_mono from pi-mono-rust..."
cd "$SCRIPT_DIR/pi-mono-rust"
uv pip install maturin
maturin develop --features python

# Install cleon and run tests
cd "$SCRIPT_DIR/python"
uv pip install -e .
uv pip install pytest

echo "Running live tests..."
PIMONO_LIVE_TEST=1 pytest tests/test_pimono_backend.py -v -k "Live or EventStreaming or SessionResume"
