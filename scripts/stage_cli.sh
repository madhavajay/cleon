#!/usr/bin/env bash
set -euo pipefail

# Build the cleon CLI binary for the current (or specified) target and stage it
# into python/src/cleon/bin so maturin can bundle it with the wheel/sdist.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-${CARGO_BUILD_TARGET:-}}"
BIN_DIR="$ROOT/python/src/cleon/bin"
BIN_NAME="cleon"

if [[ "$TARGET" == *"windows"* ]]; then
  BIN_NAME="cleon.exe"
fi

echo "Staging cleon binary (target=${TARGET:-host})..."

if [[ -n "$TARGET" ]]; then
  cargo build --manifest-path "$ROOT/Cargo.toml" --bin cleon --release --target "$TARGET"
  SRC_BIN="$ROOT/target/$TARGET/release/$BIN_NAME"
else
  cargo build --manifest-path "$ROOT/Cargo.toml" --bin cleon --release
  SRC_BIN="$ROOT/target/release/$BIN_NAME"
fi

if [[ ! -f "$SRC_BIN" ]]; then
  echo "error: built binary not found at $SRC_BIN" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
rm -f "$BIN_DIR/cleon" "$BIN_DIR/cleon.exe"
cp "$SRC_BIN" "$BIN_DIR/$BIN_NAME"
echo "Staged $BIN_NAME to $BIN_DIR"
