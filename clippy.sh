#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: cargo not found in PATH" >&2
  exit 1
fi

cargo fmt -p cleon
cargo fmt --manifest-path "$ROOT/python/cleon/Cargo.toml"

cargo clippy -p cleon --all-targets --all-features -- -D warnings
cargo clippy --manifest-path "$ROOT/python/cleon/Cargo.toml" --all-targets --all-features -- -D warnings
