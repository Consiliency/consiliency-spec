#!/usr/bin/env bash
# DABI C-ABI smoke gate: build canon-core with the `c-binding` feature, regenerate the header
# deterministically (fail if it drifts from the checked-in header), then compile + run the C smoke
# test against the built cdylib. Proves the extern "C" surface is byte-identical to the engine.
#
# Run from anywhere:  bash canon/core/scripts/check_cabi_smoke.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$HERE/.." && pwd)"          # canon/core
cd "$CORE"

echo "[1/4] cargo build --features c-binding"
cargo build -p canon-core --features c-binding

echo "[2/4] regenerate the C header and assert it matches the checked-in copy"
if command -v cbindgen >/dev/null 2>&1; then
  TMP_HEADER="$(mktemp)"
  cbindgen --config cbindgen.toml --crate canon-core --output "$TMP_HEADER" . 2>/dev/null
  if ! diff -u include/canon_core.h "$TMP_HEADER"; then
    echo "ERROR: canon_core.h is stale — regenerate with cbindgen and commit the result." >&2
    rm -f "$TMP_HEADER"
    exit 1
  fi
  rm -f "$TMP_HEADER"
  echo "      header is deterministic (matches checked-in copy)"
else
  echo "      cbindgen not installed; skipping header-determinism check (compile still validates it)"
fi

# Locate the built cdylib (workspace cargo target may be redirected via CARGO_TARGET_DIR).
TARGET_DIR="${CARGO_TARGET_DIR:-$CORE/target}"
LIB=""
for cand in "$TARGET_DIR/debug/libcanon_core.so" "$TARGET_DIR/debug/libcanon_core.dylib"; do
  [ -f "$cand" ] && LIB="$cand" && break
done
if [ -z "$LIB" ]; then
  echo "ERROR: could not find the built canon_core cdylib under $TARGET_DIR/debug" >&2
  exit 1
fi
LIBDIR="$(dirname "$LIB")"

echo "[3/4] compile the C smoke test against $LIB"
CC="${CC:-cc}"
OUT="$(mktemp -d)/c_abi_smoke"
"$CC" -std=c11 -Wall -Wextra -I include tests/c_abi_smoke.c -L "$LIBDIR" -lcanon_core -o "$OUT"

echo "[4/4] run the C-ABI smoke test"
LD_LIBRARY_PATH="$LIBDIR:${LD_LIBRARY_PATH:-}" DYLD_LIBRARY_PATH="$LIBDIR:${DYLD_LIBRARY_PATH:-}" "$OUT"
