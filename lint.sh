#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Python linting
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 Linting Python (cleon)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

export UV_VENV_CLEAR=1
uv venv .venv-lint
source .venv-lint/bin/activate
uv pip install -e ./python
uv pip install pytest ruff mypy vulture

cd "$SCRIPT_DIR/python"

echo "Running ruff format..."
ruff format .

echo "Running ruff check with fixes..."
ruff check . --fix

echo "Running mypy..."
mypy .

echo "Running vulture to detect dead code..."
vulture src tests --min-confidence 80

# TypeScript/Extension linting
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 Linting TypeScript (extension)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$SCRIPT_DIR/extension"

echo "Installing npm dependencies..."
npm install

echo "Running ESLint..."
npm run lint:fix

echo ""
echo "✅ All linting checks passed!"