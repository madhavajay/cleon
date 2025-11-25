#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Cleon Cell Control Extension Installer ==="
echo ""

# Check for required tools
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "❌ $1 not found. Please install it first."
        exit 1
    fi
}

check_command python
check_command node
check_command npm

echo "✓ Prerequisites found"
echo ""

# Check JupyterLab version
JUPYTERLAB_VERSION=$(python -c "import jupyterlab; print(jupyterlab.__version__)" 2>/dev/null || echo "not installed")
echo "JupyterLab version: $JUPYTERLAB_VERSION"

if [[ "$JUPYTERLAB_VERSION" == "not installed" ]]; then
    echo "❌ JupyterLab not installed. Install with: pip install jupyterlab>=4.0"
    exit 1
fi

MAJOR_VERSION=$(echo "$JUPYTERLAB_VERSION" | cut -d. -f1)
if [[ "$MAJOR_VERSION" -lt 4 ]]; then
    echo "⚠️  Warning: This extension is built for JupyterLab 4.x"
    echo "   You have JupyterLab $JUPYTERLAB_VERSION"
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "=== Installing Node dependencies ==="
npm install

echo ""
echo "=== Building TypeScript ==="
npm run clean:lib || true
npm run build:lib:prod

echo ""
echo "=== Building JupyterLab extension ==="
python -m jupyter labextension build .

echo ""
echo "=== Installing Python package ==="
pip install -e .

echo ""
echo "=== Verifying installation ==="
python -m jupyter labextension list 2>&1 | grep -i cleon || echo "(Extension should appear after JupyterLab restart)"

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Usage in notebook:"
echo "  from cleon_cell_control import insert_and_run"
echo "  insert_and_run(\"print('Hello!')\")"
echo ""
echo "Restart JupyterLab if it's running."
