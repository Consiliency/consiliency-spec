#!/usr/bin/env bash
# PUBLISHED-PACKAGE PARITY gate for canon-core (GP-canon GATE phase).
#
# The XG4 source gate (check_xg4_canon_core.sh) BUILDS + EXECUTES the PyO3/WASM bindings FROM SOURCE
# and diffs bytes+digests. It is blind to the PUBLISH pipeline (publish-canon-core.yml), a SEPARATE
# code path that has already shipped bugs — a corpus dropped from the npm `files` list, a --provenance
# failure, a job-permissions checkout failure. Those never touch the source build, so the source gate
# cannot see them. This gate closes that hole: it installs the PACKAGES CONSUMERS ACTUALLY INSTALL and
# holds them to the same byte/digest oracle.
#
# What it verifies, pinned to the EXACT published version (so a yank/re-publish is caught):
#   1. `@consiliency/canon-core@0.1.0` from npm, in a temp install, run through the corpus SHIPPED
#      INSIDE the tarball (the authoritative oracle) via canonicalBytesFromJson/digestFromJson in Node
#      — byte(b64)+digest(hex) identity for every valid vector, rejection for every expect_error one.
#   2. `consiliency-canon-core==0.1.0` from PyPI (WHEEL only — never a source build), in a venv, same
#      corpus, same assertions, via canon_core.canonical_bytes_from_json/digest_from_json in Python.
#   3. The npm-shipped corpus, the wheel-shipped corpus, and spec's canonical corpus are all
#      byte-identical (a publish shipping a stale/wrong corpus fails here).
#   4. The two published engines agree byte-for-byte on both the corpus and the engine-level BOUNDARY
#      vectors (engine_boundary_vectors.json): the tag-vs-literal-$int-key razor, 2^53+/-1 exact big
#      integers, and nesting past the serde recursion ceiling (which MUST reject, not mis-digest).
#   5. Count guards (>=30 valid, >=6 error) so a truncated corpus cannot silently pass.
#
# NETWORK: this gate installs from npm + PyPI. It is wired as a REQUIRED step on the HOSTED CI runner
# (ubuntu-latest), which has egress, so it always runs there. Where the registries are unreachable
# (an offline Dagger container, a firewalled dev box) it prints a clear SKIP and exits 0 — a REGISTRY
# being unreachable is a skip; a reachable registry MISSING the pinned version (yank) is a hard FAIL.
#
# Local run: set CANON_PY to an interpreter that has a published wheel (0.1.0 ships cp312 wheels), e.g.
#   CANON_PY=python3.12 bash canon/conformance/check_published_canon_core.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="$ROOT/canon/conformance"
SPEC_CORPUS="$ROOT/canon/vectors/canon-vectors.json"
BOUNDARY="$CONF/engine_boundary_vectors.json"

NPM_PKG="@consiliency/canon-core"
PYPI_PKG="consiliency-canon-core"
VERSION="0.1.0"
PYBIN="${CANON_PY:-python3}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== canon-core PUBLISHED-PACKAGE parity gate =="
echo "root:    $ROOT"
echo "pinned:  npm $NPM_PKG@$VERSION | PyPI $PYPI_PKG==$VERSION"
echo "python:  $PYBIN ($($PYBIN --version 2>&1))"
echo "node:    $(node --version 2>/dev/null || echo 'MISSING')"
echo

command -v node >/dev/null 2>&1 || { echo "FAIL: node is required for the npm leg"; exit 1; }
command -v npm  >/dev/null 2>&1 || { echo "FAIL: npm is required for the npm leg"; exit 1; }
command -v "$PYBIN" >/dev/null 2>&1 || { echo "FAIL: interpreter '$PYBIN' not found (set CANON_PY)"; exit 1; }

# --- Preflight: registry reachability + pinned-version existence + wheel compatibility -------------
# Prints one verdict line and exits:  0 = PROCEED, 10 = SKIP (env/network), 11 = FAIL (yank/missing).
set +e
PRE_OUT="$("$PYBIN" - "$VERSION" <<'PY'
import json, sys, urllib.request, urllib.error
version = sys.argv[1]
py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"

def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return ("ok", r.read())
    except urllib.error.HTTPError as e:      # server RESPONDED (e.g. 404) => registry is reachable
        return ("http", e.code)
    except Exception:                        # DNS/TCP/TLS/timeout => registry unreachable
        return ("unreachable", None)

# npm registry
st, body = fetch("https://registry.npmjs.org/@consiliency%2Fcanon-core")
if st == "unreachable":
    print("SKIP: npm registry unreachable — published-parity gate cannot run"); sys.exit(10)
if st == "http":
    print(f"FAIL: npm registry returned HTTP {body} for @consiliency/canon-core"); sys.exit(11)
if version not in json.loads(body).get("versions", {}):
    print(f"FAIL: npm @consiliency/canon-core@{version} is not published (yanked/unpublished?)"); sys.exit(11)

# PyPI
st, body = fetch("https://pypi.org/pypi/consiliency-canon-core/json")
if st == "unreachable":
    print("SKIP: PyPI unreachable — published-parity gate cannot run"); sys.exit(10)
if st == "http":
    print(f"FAIL: PyPI returned HTTP {body} for consiliency-canon-core"); sys.exit(11)
rel = json.loads(body).get("releases", {}).get(version) or []
non_yanked = [f for f in rel if not f.get("yanked", False)]
if not non_yanked:
    print(f"FAIL: PyPI consiliency-canon-core=={version} has no live files (yanked/unpublished?)"); sys.exit(11)
wheels = [f for f in non_yanked if f["filename"].endswith(".whl")]
if not any(py_tag in f["filename"] for f in wheels):
    # Distinct from an unreachable registry (10): the registries ARE reachable and 0.1.0 IS
    # published, but this interpreter has no matching wheel. Exit 12 so the caller can make it a
    # HARD FAIL under CANON_STRICT (a runner we control must not vacuously green on a wheel skip),
    # while a genuinely-unreachable registry (10) always stays a graceful skip.
    print(f"SKIP: no {py_tag} wheel for consiliency-canon-core=={version} "
          f"(interpreter {sys.version.split()[0]}); set CANON_PY to an interpreter with a published wheel"); sys.exit(12)
print("PROCEED"); sys.exit(0)
PY
)"
PRE_RC=$?
set -e
echo "$PRE_OUT"
case "$PRE_RC" in
  0)  : ;;
  10) echo; echo "PUBLISHED-PARITY GATE SKIPPED (registry unreachable; hosted CI has egress and runs it)."; exit 0 ;;
  11) echo; echo "PUBLISHED-PARITY GATE FAILED (pinned version missing at a reachable registry)."; exit 1 ;;
  12) # wheel-mismatch skip. On a runner we control (CANON_STRICT=1) this is a HARD FAIL — a required
      # check must never vacuously green because the interpreter lacks a published wheel. Elsewhere
      # (a dev box on an unsupported Python) it degrades to a graceful skip.
      if [ "${CANON_STRICT:-0}" = "1" ]; then
        echo; echo "PUBLISHED-PARITY GATE FAILED (CANON_STRICT=1 and no wheel for this interpreter — expected a published wheel here)."; exit 1
      fi
      echo; echo "PUBLISHED-PARITY GATE SKIPPED (no wheel for this interpreter; set CANON_PY locally)."; exit 0 ;;
  *)  echo "FAIL: unexpected preflight status $PRE_RC"; echo "$PRE_OUT"; exit 1 ;;
esac
echo

# One retry to ride out transient registry blips (the check is always-on on the hosted leg).
retry() {
  local n=1
  until "$@"; do
    [ "$n" -ge 2 ] && return 1
    echo "    (install attempt $n failed; retrying once...)"; n=$((n + 1)); sleep 3
  done
}

# --- [1] Install the PUBLISHED npm package into a temp dir -----------------------------------------
echo "[1/6] Install PUBLISHED npm $NPM_PKG@$VERSION"
NPMDIR="$TMP/npm"; mkdir -p "$NPMDIR"
printf '{"name":"canon-published-gate","version":"0.0.0","private":true}\n' > "$NPMDIR/package.json"
retry npm install --prefix "$NPMDIR" "$NPM_PKG@$VERSION" --no-audit --no-fund >/dev/null 2>&1 \
  || { echo "FAIL: npm install $NPM_PKG@$VERSION failed"; exit 1; }
NPM_PKG_DIR="$NPMDIR/node_modules/@consiliency/canon-core"
NPM_CORPUS="$NPM_PKG_DIR/canon-vectors.json"
[ -f "$NPM_CORPUS" ] || { echo "FAIL: npm tarball did not ship canon-vectors.json (publish files-list bug!)"; exit 1; }
echo "    installed -> $NPM_PKG_DIR"
echo

# --- [2] Install the PUBLISHED PyPI WHEEL into a venv (never a source build) -----------------------
echo "[2/6] Install PUBLISHED PyPI $PYPI_PKG==$VERSION (wheel only)"
VENV="$TMP/venv"
"$PYBIN" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
retry "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --only-binary=:all: "$PYPI_PKG==$VERSION" \
  || { echo "FAIL: pip install $PYPI_PKG==$VERSION (wheel) failed"; exit 1; }
WHEEL_CORPUS="$("$VENV/bin/python" - <<'PY'
import sys, glob, os
hits = [f for base in sys.path if "site-packages" in base
        for f in glob.glob(os.path.join(base, "canon-vectors.json"))]
print(hits[0] if hits else "")
PY
)"
if [ -z "$WHEEL_CORPUS" ] || [ ! -f "$WHEEL_CORPUS" ]; then
  echo "FAIL: wheel did not ship canon-vectors.json (maturin include bug!)"; exit 1
fi
echo "    installed wheel; corpus -> $WHEEL_CORPUS"
echo

# --- [3] Corpus byte-identity: spec canonical == npm-shipped == wheel-shipped ----------------------
echo "[3/6] Corpus byte-identity (spec == npm-shipped == wheel-shipped)"
h_spec="$(sha256sum "$SPEC_CORPUS"  | awk '{print $1}')"
h_npm="$( sha256sum "$NPM_CORPUS"   | awk '{print $1}')"
h_wheel="$(sha256sum "$WHEEL_CORPUS" | awk '{print $1}')"
echo "    spec  $h_spec"
echo "    npm   $h_npm"
echo "    wheel $h_wheel"
if [ "$h_spec" != "$h_npm" ] || [ "$h_spec" != "$h_wheel" ]; then
  echo "FAIL: published corpus diverged from spec's canonical corpus — a publish shipped a stale/wrong corpus."
  exit 1
fi
echo "    all three corpora are byte-identical."
echo

# --- [4] Run the PUBLISHED npm engine over the SHIPPED corpus + boundary vectors -------------------
echo "[4/6] PUBLISHED npm engine (Node) vs the package-shipped oracle + boundary vectors"
( cd "$NPMDIR" && node "$CONF/emit_published_npm.mjs" \
    "$NPM_CORPUS" "$BOUNDARY" "$TMP/npm_corpus.txt" "$TMP/npm_boundary.txt" )
echo

# --- [5] Run the PUBLISHED PyPI wheel over the SHIPPED corpus + boundary vectors -------------------
echo "[5/6] PUBLISHED PyPI wheel (Python) vs the wheel-shipped oracle + boundary vectors"
"$VENV/bin/python" "$CONF/emit_published_py.py" \
  "$WHEEL_CORPUS" "$BOUNDARY" "$TMP/wheel_corpus.txt" "$TMP/wheel_boundary.txt"
echo

# --- [6] Cross-engine parity: the two PUBLISHED engines must agree byte-for-byte -------------------
echo "[6/6] Cross-engine parity (published npm == published wheel) on corpus + boundary emits"
diff -u "$TMP/npm_corpus.txt"   "$TMP/wheel_corpus.txt"
diff -u "$TMP/npm_boundary.txt" "$TMP/wheel_boundary.txt"
echo "    npm and wheel engines agree on $(wc -l < "$TMP/npm_corpus.txt" | tr -d ' ') corpus"
echo "    and $(wc -l < "$TMP/npm_boundary.txt" | tr -d ' ') boundary emit lines."
echo

echo "PUBLISHED-PACKAGE PARITY GATE GREEN"
