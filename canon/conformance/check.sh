#!/usr/bin/env bash
#
# canon v2 EXIT GATE (THE fleet gate, SPEC.md section 11).
#
# canon v2 relocated mandatory Unicode NFC OUT of the hash to the ingestion boundary
# (canon/py/ingest.py), so canon itself is Unicode-DB-independent. The gate asserts:
#   1. Python canon passes its own conformance (verbatim bytes + digest match the pinned vectors).
#   2. TypeScript canon passes its own conformance (same pinned vectors).
#   3. INGEST-boundary NFC is enforced and load-bearing: non-NFC input is normalized at ingest
#      (test_ingest_nfc.py), and the pinned Unicode DB / post-13 skew discriminator is live and the
#      fail-closed version assertion fires (test_unicode_skew.py). This is what the byte-identity
#      check below STRUCTURALLY CANNOT prove — canon v2 passes bytes through verbatim, so both ports
#      stay byte-identical even if ingest NFC were missing entirely.
#   4. Python and TypeScript canon produce BYTE-IDENTICAL output + IDENTICAL digests on every vector
#      (the cross-language byte-identity gate).
#
# Exit 0 = gate green. Any divergence -> non-zero with a diff. No fudging.

set -euo pipefail

CANON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$CANON_DIR/py"
TS="$CANON_DIR/ts"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== canon v2 conformance gate =="
echo "canon dir: $CANON_DIR"
echo

# --- 0. Pinned Unicode DB for the INGEST boundary (fail-closed) ---
# canon v2 no longer needs a Unicode DB, but the INGEST boundary (canon/py/ingest.py) does — that is
# where NFC now lives, pinned to Node's Unicode version. Install the exact pin so the ingest tests in
# step 3 are reproducible in a clean CI (not just where it happens to be present).
echo "[0/4] Pinned Unicode DB for the ingest boundary (unicodedata2)"
python3 -m pip install --quiet --disable-pip-version-check -r "$PY/requirements.txt"
python3 -c "import unicodedata2; print('    unicodedata2', unicodedata2.unidata_version)"
echo

# --- 1. Python canon self-conformance ---
echo "[1/4] Python canon conformance (vs pinned vectors)"
python3 "$PY/test_canon.py"
echo

# --- 2. TypeScript canon self-conformance ---
echo "[2/4] TypeScript canon conformance (vs pinned vectors)"
npx --yes tsx "$TS/canon.test.ts"
echo

# --- 3. Ingest-boundary NFC enforcement (the byte-identity gate CANNOT prove this) ---
# canon v2 emits bytes verbatim, so step 4 would stay green even if ingest NFC vanished. These two
# tests are the ONLY proof that NFC enforcement actually lives at the ingest boundary and that its
# pinned Unicode DB is load-bearing: test_ingest_nfc feeds NON-NFC input through ingest->canon and
# asserts the normalized form; test_unicode_skew proves the fail-closed pin assertion fires on a
# stale DB and that the post-13 inputs are live U13-vs-U16 discriminators.
echo "[3/4] Ingest-boundary NFC enforcement + pinned-DB skew proof"
python3 "$CANON_DIR/conformance/test_ingest_nfc.py"
python3 "$CANON_DIR/conformance/test_unicode_skew.py"
echo

# --- 4. Cross-language byte-identity ---
echo "[4/4] Cross-language byte-identity (Python emit vs TypeScript emit)"
python3 "$PY/test_canon.py" --emit > "$TMP/py.json"
npx --yes tsx "$TS/canon.test.ts" --emit > "$TMP/ts.json"

if diff -u "$TMP/py.json" "$TMP/ts.json" > "$TMP/diff.txt"; then
  N=$(wc -l < "$TMP/py.json" | tr -d ' ')
  echo "    Python and TypeScript agree byte-for-byte on all $N vectors."
  echo
  echo "CANON v2 CONFORMANCE GATE: PASS"
  exit 0
else
  echo "    DIVERGENCE between Python and TypeScript:"
  cat "$TMP/diff.txt"
  echo
  echo "CANON v2 CONFORMANCE GATE: FAIL"
  exit 1
fi
