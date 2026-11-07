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
#   4. The committed vector corpus IS the generator's output (gen_vectors.py --check): a hand-edited
#      vector, or a generator change without regeneration, is a silent corpus/reference drift that
#      steps 1-2 cannot see (they test against whatever is committed).
#   5. Python and TypeScript canon produce BYTE-IDENTICAL output + IDENTICAL digests on every vector
#      (the cross-language byte-identity gate).
#
# Exit 0 = gate green. Any divergence -> non-zero with a diff. No fudging.

set -euo pipefail

CANON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$CANON_DIR")"
PY="$CANON_DIR/py"
# The one sanctioned Python import root, src/ (the spec_core package): the ingest-boundary tests and
# the vector generator import spec_core.canon. This script is
# public and cannot source ci/lib/pyroot.sh, so it sets the same value inline.
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TS="$CANON_DIR/ts"
TMP="$(mktemp -d)"
# tsx pinned exactly (CI-5): a bare `npx --yes tsx` runs whatever npm
# resolves that day. Every script that runs tsx carries the same pin (ci/check_ci_hygiene.py).
TSX="tsx@4.23.15"
trap 'rm -rf "$TMP"' EXIT

echo "== canon v2 conformance gate =="
echo "canon dir: $CANON_DIR"
echo

# --- 0. Pinned Unicode DB for the INGEST boundary (fail-closed) ---
# canon v2 no longer needs a Unicode DB, but the INGEST boundary (canon/py/canon_ingest.py) does — that
# is where NFC now lives, pinned to Node's Unicode version. Install the pin (requirements.txt: the
# unicodedata2 backport, which its marker leaves out on CPython 3.14), then load the ingest boundary
# itself: its own fail-closed rule decides whether this interpreter has a Unicode 16.0 database (the
# backport, or a stdlib that already reports 16.0 when the backport is absent) and which one it uses.
echo "[0/5] Pinned Unicode DB for the ingest boundary"
python3 -m pip install --quiet --disable-pip-version-check -r "$PY/requirements.txt"
python3 -c "from spec_core.canon import canon_ingest as i; print('    ingest Unicode DB:', i.UNICODE_DB_SOURCE, i.unicodedata.unidata_version)"
# SPEC_EXPECT_UNICODE_DB (set by CI on CPython 3.14: "stdlib unicodedata") pins WHICH database the gate
# runs on, so CI proves the stdlib path for real: the backport must not even be importable, and the
# ingest boundary must have chosen the stdlib.
if [ -n "${SPEC_EXPECT_UNICODE_DB:-}" ]; then
  python3 - "$SPEC_EXPECT_UNICODE_DB" <<'EXPECT'
import importlib.util, sys
from spec_core.canon import canon_ingest as i
want = sys.argv[1]
if i.UNICODE_DB_SOURCE != want:
    sys.exit("FAIL: ingest loaded %r, but SPEC_EXPECT_UNICODE_DB is %r" % (i.UNICODE_DB_SOURCE, want))
if want == "stdlib unicodedata" and importlib.util.find_spec("unicodedata2") is not None:
    sys.exit("FAIL: SPEC_EXPECT_UNICODE_DB is the stdlib, but unicodedata2 is importable in this interpreter")
print("    SPEC_EXPECT_UNICODE_DB=%s: confirmed (unicodedata2 importable: %s)"
      % (want, importlib.util.find_spec("unicodedata2") is not None))
EXPECT
fi
echo

# --- 1. Python canon self-conformance ---
echo "[1/5] Python canon conformance (vs pinned vectors)"
# canon/py/canon.py and canon_ingest.py are byte mirrors of src/spec_core/canon/: test_canon.py runs
# the mirror by path, steps 3 and 4 import the package, so the two must be the same bytes.
for f in canon.py canon_ingest.py; do
  cmp -s "$PY/$f" "$ROOT/src/spec_core/canon/$f" \
    || { echo "FAIL: canon/py/$f is not a byte copy of src/spec_core/canon/$f" >&2; exit 1; }
done
python3 "$PY/test_canon.py"
echo

# --- 2. TypeScript canon self-conformance ---
echo "[2/5] TypeScript canon conformance (vs pinned vectors)"
npx --yes "$TSX" "$TS/canon.test.ts"
echo

# --- 3. Ingest-boundary NFC enforcement (the byte-identity gate CANNOT prove this) ---
# canon v2 emits bytes verbatim, so step 4 would stay green even if ingest NFC vanished. These two
# tests are the ONLY proof that NFC enforcement actually lives at the ingest boundary and that its
# pinned Unicode DB is load-bearing: test_ingest_nfc feeds NON-NFC input through ingest->canon and
# asserts the normalized form; test_unicode_skew proves the fail-closed pin assertion fires on a
# stale DB and that the post-13 inputs are live U13-vs-U16 discriminators.
echo "[3/5] Ingest-boundary NFC enforcement + pinned-DB skew proof"
python3 "$CANON_DIR/conformance/test_ingest_nfc.py"
python3 "$CANON_DIR/conformance/test_unicode_skew.py"
echo

# --- 4. Vector corpus is the generator's output ---
echo "[4/5] Vector corpus is the generator's output (gen_vectors.py --check)"
python3 "$CANON_DIR/vectors/gen_vectors.py" --check
echo

# --- 5. Cross-language byte-identity ---
echo "[5/5] Cross-language byte-identity (Python emit vs TypeScript emit)"
python3 "$PY/test_canon.py" --emit > "$TMP/py.json"
npx --yes "$TSX" "$TS/canon.test.ts" --emit > "$TMP/ts.json"

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
