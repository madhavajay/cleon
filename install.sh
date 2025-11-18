#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PREFIX="${INSTALL_PREFIX:-$HOME/.local/bin}"
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet)
      QUIET=1
      shift
      ;;
    *)
      echo "Usage: $0 [--quiet]" >&2
      exit 1
      ;;
  esac
done

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    echo "$@"
  fi
}

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: cargo not found in PATH" >&2
  exit 1
fi

if ! command -v maturin >/dev/null 2>&1; then
  log "maturin not found; installing into .maturin-venv..."
  export MATURIN_VENV="$ROOT/.maturin-venv"
  python3 -m venv "$MATURIN_VENV"
  "$MATURIN_VENV/bin/pip" install --upgrade pip maturin
  export PATH="$MATURIN_VENV/bin:$PATH"
fi

cargo build --release ${QUIET:+--quiet}
maturin build --release --manifest-path "$ROOT/python/cleon/Cargo.toml" --out "$ROOT/dist" ${QUIET:+--quiet}
WHEEL="$(ls -t "$ROOT"/dist/cleon-*.whl | head -n1)"
uv pip install --reinstall "$WHEEL" ${QUIET:+--quiet}

log "Installed successfully."
