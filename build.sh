#!/usr/bin/env bash
set -euo pipefail

# Build both cleon wheel and extension wheel for local testing

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$ROOT/dist"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║            Building cleon packages for testing             ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Clean dist directory
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# 1. Build cleon wheel
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 Building cleon wheel..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Stage CLI binary
"$ROOT/scripts/stage_cli.sh"

# Build wheel using maturin (prefer uvx, fall back to direct command)
cd "$ROOT"
if command -v uvx &> /dev/null; then
    uvx maturin build --release --out "$DIST_DIR" --manifest-path python/cleon/Cargo.toml
elif command -v maturin &> /dev/null; then
    maturin build --release --out "$DIST_DIR" --manifest-path python/cleon/Cargo.toml
else
    echo "❌ maturin not found. Install with: pip install maturin (or use uvx)"
    exit 1
fi

CLEON_WHEEL=$(ls "$DIST_DIR"/cleon-*.whl 2>/dev/null | head -1)
echo "✅ Built: $CLEON_WHEEL"
echo ""

# 2. Build extension wheel
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 Building cleon-jupyter-extension wheel..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$ROOT/extension"

# Install npm dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Build TypeScript
echo "Building TypeScript..."
npm run build:lib:prod

# Build JupyterLab extension
echo "Building JupyterLab extension..."
python -m jupyter labextension build .

# Build Python wheel
echo "Building Python wheel..."
python -m build --outdir "$DIST_DIR"

EXT_WHEEL=$(ls "$DIST_DIR"/cleon_jupyter_extension-*.whl 2>/dev/null | head -1)
echo "✅ Built: $EXT_WHEEL"
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Build complete! Wheels are in: $DIST_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
ls -la "$DIST_DIR"/*.whl
echo ""
echo "Copy & paste to install:"
echo ""
echo "uv pip install -U $CLEON_WHEEL"
echo "uv pip install -U $EXT_WHEEL"
