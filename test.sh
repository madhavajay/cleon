#!/usr/bin/env bash
set -euo pipefail

cd python
uv venv --allow-existing
uv pip install -e .
uv run --with pytest pytest
