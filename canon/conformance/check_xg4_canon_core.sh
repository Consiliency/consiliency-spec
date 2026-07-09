#!/usr/bin/env bash
# XG4 canon-core conformance gate.
#
# Proves the Rust canon-core is byte-identical to the Python + TypeScript references AND to its OWN
# BUILT bindings (PyO3 .so, WASM module) on the pinned vector corpus — and cross-repo, on the
# downstream consumer vectors. The bindings are what downstream consumers actually import, so the gate
# BUILDS and EXECUTES them (not just `cargo check`), diffing their emitted bytes+digests against the
# canon v2 reference. This closes the gap where a binding could COMPILE yet emit different bytes (a
# JSON-boundary marshalling bug, a Vec<u8>->Uint8Array mistake) — invisible to `cargo check`.
#
# It also surfaces the SPEC §5.0 lone-surrogate BOUNDARY finding: the WASM `&str` surface is lossy
# (wasm-bindgen replaces a lone surrogate with U+FFFD before Rust runs), so WASM silently accepts what
# the enforcing pure-JS v2 port and Python reject. See canon/conformance/wasm_surrogate_finding.mjs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE="$ROOT/canon/core/Cargo.toml"
CONF="$ROOT/canon/conformance"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# cargo target dir may be redirected (e.g. CARGO_TARGET_DIR onto a build volume); discover it rather
# than assuming canon/core/target so the BUILT artifacts are located wherever cargo actually put them.
TARGET_DIR="$(cargo metadata --no-deps --format-version=1 --manifest-path "$CORE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"

# The cross-repo downstream-consumer corpus is wired as a REQUIRED check against a COMMITTED, pinned
# copy vendored into this repo (canon/vectors/gov-cross-repo-canon-vectors.json + its PROVENANCE.md) —
# the same pattern spec already uses for the vendored authority contract vectors. Vendoring is
# what makes the check reliably present, and therefore REQUIRED and CI-green, without any sibling
# checkout. Its vectors are canon v1 (in-hash NFC); canon-core is v2 (no in-hash NFC), so the DIGEST
# domains differ and the NFC vectors' BYTES differ BY DESIGN — we therefore hold every canon v2 engine
# (Python ref, TS ref, Rust core, PyO3, WASM) byte+digest-IDENTICAL to each OTHER over the corpus INPUT
# domain (Python v2 reference as baseline), the cross-repo serialization-parity invariant. When a live
# corpus IS provided (GOV_CORPUS env — opt-in, no hardcoded path), an OPTIONAL cross-check asserts the
# live file is byte-identical to the committed copy, catching upstream drift at the source.
GOV_CORPUS_VENDORED="$ROOT/canon/vectors/gov-cross-repo-canon-vectors.json"
GOV_CORPUS_LIVE="${GOV_CORPUS:-}"

echo "== XG4 canon-core conformance gate =="
echo "root: $ROOT"
echo "target: $TARGET_DIR"
echo

echo "[1/8] Existing Python + TypeScript canon gate"
bash "$ROOT/canon/conformance/check.sh"
echo

echo "[2/8] Rust canon-core tests"
cargo test --manifest-path "$CORE"
echo

echo "[3/8] Python/TypeScript/Rust byte-identity on spec-local vectors"
SPEC_CORPUS="$ROOT/canon/vectors/canon-vectors.json"
python3 "$ROOT/canon/py/test_canon.py" --emit > "$TMP/python.txt"
npx --yes tsx "$ROOT/canon/ts/canon.test.ts" --emit > "$TMP/typescript.txt"
cargo run --quiet --manifest-path "$CORE" --bin canon_core_emit -- "$SPEC_CORPUS" > "$TMP/rust.txt"
diff -u "$TMP/python.txt" "$TMP/typescript.txt"
diff -u "$TMP/python.txt" "$TMP/rust.txt"
echo "    Python, TypeScript, and Rust agree on $(wc -l < "$TMP/rust.txt" | tr -d ' ') vectors."
echo

# --- BUILD + EXECUTE the shipped bindings (was: cargo check only) -------------------------------- #

echo "[4/8] BUILD + EXECUTE the PyO3 binding, diff its emitted bytes+digests vs the Python reference"
PYO3_PYTHON="$(command -v python3)" cargo build --manifest-path "$CORE" --features pyo3-binding --lib
SO_SRC="$TARGET_DIR/debug/libcanon_core.so"
[[ -f "$SO_SRC" ]] || { echo "FAIL: PyO3 cdylib not found at $SO_SRC"; exit 1; }
SO_DIR="$TMP/pyo3"; mkdir -p "$SO_DIR"; cp "$SO_SRC" "$SO_DIR/canon_core.so"
PYTHONPATH="$SO_DIR" python3 "$CONF/emit_pyo3.py" "$SPEC_CORPUS" > "$TMP/pyo3.txt"
diff -u "$TMP/python.txt" "$TMP/pyo3.txt"
echo "    BUILT PyO3 binding is byte-identical to the Python reference on all spec-local vectors."
echo

echo "[5/8] BUILD + EXECUTE the WASM binding, diff its emitted bytes+digests vs the TypeScript reference"
if command -v rustup >/dev/null 2>&1; then rustup target add wasm32-unknown-unknown >/dev/null; fi
# wasm-bindgen-cli generates the Node glue that marshals the &str/Vec<u8>/Result surface; its version
# MUST match the wasm-bindgen crate (Cargo.lock), or the schema check fails. Ensure it, like the target.
WB_VERSION="$(python3 - "$ROOT/canon/core/Cargo.lock" <<'PY'
import re, sys
lock = open(sys.argv[1]).read()
m = re.search(r'name = "wasm-bindgen"\nversion = "([^"]+)"', lock)
print(m.group(1) if m else "")
PY
)"
if ! command -v wasm-bindgen >/dev/null 2>&1 || [[ "$(wasm-bindgen --version | awk '{print $2}')" != "$WB_VERSION" ]]; then
  echo "    installing wasm-bindgen-cli $WB_VERSION (must match the wasm-bindgen crate)"
  cargo install wasm-bindgen-cli --version "$WB_VERSION" --locked >/dev/null 2>&1 || cargo install wasm-bindgen-cli --version "$WB_VERSION" >/dev/null
fi
cargo build --manifest-path "$CORE" --features wasm-binding --target wasm32-unknown-unknown --lib
WASM_SRC="$TARGET_DIR/wasm32-unknown-unknown/debug/canon_core.wasm"
[[ -f "$WASM_SRC" ]] || { echo "FAIL: WASM artifact not found at $WASM_SRC"; exit 1; }
GLUE="$TMP/wasm"; mkdir -p "$GLUE"
wasm-bindgen "$WASM_SRC" --target nodejs --out-dir "$GLUE"
node "$CONF/emit_wasm.mjs" "$GLUE" "$SPEC_CORPUS" > "$TMP/wasm.txt"
diff -u "$TMP/typescript.txt" "$TMP/wasm.txt"
echo "    BUILT WASM binding is byte-identical to the TypeScript reference on all spec-local vectors."
echo

echo "[6/8] SPEC §5.0 lone-surrogate BOUNDARY finding (through the BUILT WASM binding)"
npx --yes tsx "$CONF/wasm_surrogate_finding.mjs" "$GLUE" "$ROOT/canon/ts/canon.ts"
echo

echo "[7/8] REQUIRED cross-repo parity on the vendored downstream-consumer corpus (canon v2 5-way byte-identity)"
if [[ ! -f "$GOV_CORPUS_VENDORED" ]]; then
  echo "FAIL: required vendored cross-repo corpus not found: $GOV_CORPUS_VENDORED"
  exit 1
fi
python3 "$ROOT/canon/py/test_canon.py" --emit "$GOV_CORPUS_VENDORED" > "$TMP/gp_python.txt"
npx --yes tsx "$ROOT/canon/ts/canon.test.ts" --emit "$GOV_CORPUS_VENDORED" > "$TMP/gp_typescript.txt"
cargo run --quiet --manifest-path "$CORE" --bin canon_core_emit -- "$GOV_CORPUS_VENDORED" > "$TMP/gp_rust.txt"
PYTHONPATH="$SO_DIR" python3 "$CONF/emit_pyo3.py" "$GOV_CORPUS_VENDORED" > "$TMP/gp_pyo3.txt"
node "$CONF/emit_wasm.mjs" "$GLUE" "$GOV_CORPUS_VENDORED" > "$TMP/gp_wasm.txt"
diff -u "$TMP/gp_python.txt" "$TMP/gp_typescript.txt"
diff -u "$TMP/gp_python.txt" "$TMP/gp_rust.txt"
diff -u "$TMP/gp_python.txt" "$TMP/gp_pyo3.txt"
diff -u "$TMP/gp_python.txt" "$TMP/gp_wasm.txt"
echo "    Python, TypeScript, Rust, BUILT-PyO3, BUILT-WASM agree on all"
echo "    $(wc -l < "$TMP/gp_rust.txt" | tr -d ' ') cross-repo downstream-consumer vectors (canon v2)."
# OPTIONAL live cross-check: if a live corpus is provided via GOV_CORPUS, the live file must be
# byte-identical to the committed copy (drift detection at the source). Absence is NOT a failure — the
# required check above already ran against the committed copy.
if [[ -n "$GOV_CORPUS_LIVE" && -f "$GOV_CORPUS_LIVE" ]]; then
  if diff -q "$GOV_CORPUS_VENDORED" "$GOV_CORPUS_LIVE" >/dev/null; then
    echo "    [live cross-check] live corpus at $GOV_CORPUS_LIVE is byte-identical to the vendored copy."
  else
    echo "FAIL: live corpus diverged from the vendored copy — re-vendor (see PROVENANCE.md):"
    echo "      vendored: $GOV_CORPUS_VENDORED"
    echo "      live:     $GOV_CORPUS_LIVE"
    exit 1
  fi
else
  echo "    [live cross-check] no live corpus provided (set GOV_CORPUS to enable); committed copy is authoritative."
fi
echo

echo "[8/8] Deliberate-mutation negative check (a divergent binding output MUST fail the gate)"
# A one-byte mutation of any engine's emit must be caught by the byte-identity diffs above; assert the
# gate has teeth rather than trusting the green above blindly.
head -1 "$TMP/rust.txt" | sed 's/./X/1' > "$TMP/mutated.txt"
if diff -q "$TMP/python.txt" "$TMP/mutated.txt" >/dev/null 2>&1; then
  echo "FAIL: byte-identity diff did not distinguish a mutated emit"; exit 1
fi
echo "    Confirmed: a mutated binding emit is distinguishable (gate is byte-exact)."
echo

echo "XG4 CANON-CORE GATE GREEN"
