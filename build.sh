#!/usr/bin/env bash
set -euo pipefail

# Build the cleon Python wheel locally with maturin.
# This mirrors the CI release step: builds from python/cleon/Cargo.toml into ./dist.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$ROOT/python/cleon/Cargo.toml"
DIST_DIR="$ROOT/dist"

if [[ ! -f "$MANIFEST" ]]; then
  echo "error: manifest not found at $MANIFEST" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required (pip install uv or https://github.com/astral-sh/uv)" >&2
  exit 1
fi

cd "$ROOT"

# Ensure a clean, dedicated venv for the build
uv venv --allow-existing

# Install maturin
uv pip install maturin

# Build and stage CLI binary for bundling
"$ROOT/scripts/stage_cli.sh"

# Build the wheel
uv run -- maturin build --release --manifest-path "$MANIFEST" --out "$DIST_DIR"

# Build sdist too (helpful for release parity)
uv run -- maturin sdist --manifest-path "$MANIFEST" --out "$DIST_DIR"

echo "✅ Wheel(s) and sdist written to $DIST_DIR"
