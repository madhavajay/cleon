#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: cargo not found in PATH" >&2
  exit 1
fi

# All Rust code is now in pi-mono-rust submodule
# Legacy src/main.rs and python/cleon/ have been removed
"$ROOT/pi-mono-rust/clippy.sh"
