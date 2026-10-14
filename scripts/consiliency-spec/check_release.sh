#!/usr/bin/env bash
# Public release gate for consiliency-spec / @consiliency/spec.
#
# Runs ONLY the self-contained conformance gates that ship in this public tree —
# no private release/publish plumbing, no internal host paths, no sibling-repo
# checkout. A public verifier can clone this repo and run this script to confirm
# canon v2 byte-identity and the outside-agent contract end to end.
#
#   - canon/conformance/check.sh          : Python <-> TypeScript canon v2 byte-identity
#   - canon/conformance/check_xg4_canon_core.sh : Rust core + BUILT PyO3/WASM bindings
#                                           byte-identical to the references, incl. the
#                                           vendored cross-repo consumer corpus.
#   - scripts/check_outside_agent_vectors.sh : outside-agent contract — schemas, the
#                                           conformance vectors, and the reference
#                                           router that derives every vector's verdict.
#   - scripts/check_certificate_vectors.sh : the reference certificate verifier
#                                           (consiliency_spec.verify_certificate) over the
#                                           public certificate vectors, incl. a tampered copy.
#   - publish dry-run                     : builds the wheel + sdist publish.yml uploads
#                                           (`python -m build`, version-checked) and runs
#                                           `npm publish --dry-run`, uploading nothing.
#
# The publish workflow (.github/workflows/publish.yml) runs this same gate before
# any Trusted-Publishing upload.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== consiliency-spec public release gate =="
bash canon/conformance/check.sh
bash canon/conformance/check_xg4_canon_core.sh
bash scripts/check_outside_agent_vectors.sh
bash scripts/check_certificate_vectors.sh

# Publish dry-run: build exactly what publish.yml uploads, without uploading it. Everything is written
# under one mktemp directory in $TMPDIR and removed on exit.
echo "== publish dry-run =="
TMP="$(mktemp -d "${TMPDIR:-/tmp}/consiliency-spec-release.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
PKG_VERSION="$(node -p "require('./package.json').version")"
PY_VERSION="$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"
if [ "$PKG_VERSION" != "$PY_VERSION" ]; then
  echo "FAIL: package.json version $PKG_VERSION != pyproject.toml version $PY_VERSION"; exit 1
fi
if ! python3 -m build --outdir "$TMP/dist" >"$TMP/build.log" 2>&1; then
  cat "$TMP/build.log"; echo "FAIL: python -m build failed"; exit 1
fi
for artifact in "consiliency_spec-${PY_VERSION}-py3-none-any.whl" "consiliency_spec-${PY_VERSION}.tar.gz"; do
  if [ ! -f "$TMP/dist/$artifact" ]; then
    ls -l "$TMP/dist"; echo "FAIL: python -m build did not produce $artifact"; exit 1
  fi
done
echo "python -m build: wheel and sdist for ${PY_VERSION}"
# `npm publish --dry-run` asks the registry whether the version exists, so once a version is published
# (until the next bump) it fails with "cannot publish over the previously published versions". That
# exact case, for exactly this version, is tolerated: the tarball was still packed and listed. Any other
# publish-side error fails the gate.
if ! npm publish --dry-run >"$TMP/npm-publish-dry-run.log" 2>&1; then
  if grep -q "cannot publish over the previously published versions: ${PKG_VERSION}\." "$TMP/npm-publish-dry-run.log"; then
    echo "npm publish --dry-run: ${PKG_VERSION} is already published; the tarball packed cleanly"
  else
    cat "$TMP/npm-publish-dry-run.log"; echo "FAIL: npm publish --dry-run failed"; exit 1
  fi
else
  echo "npm publish --dry-run: OK"
fi
echo "== consiliency-spec public release gate GREEN =="
