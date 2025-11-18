#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_ROOT="$ROOT/python"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required. install via 'pip install uv' or https://github.com/astral-sh/uv" >&2
  exit 1
fi

uv venv -p 3.13 --allow-existing

uv pip install -e "$PY_ROOT"
uv pip install --compile-bytecode ruff mypy vulture

cd "$PY_ROOT"

echo "Running ruff format..."
uv pip install ruff
uv run ruff format src

echo "Running ruff check..."
uv run ruff check --fix src

echo "Running mypy..."
uv run mypy src

echo "Running vulture to detect dead code..."
uv run vulture src --min-confidence 80

echo "✓ All linting checks passed!"
