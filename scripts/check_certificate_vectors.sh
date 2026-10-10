#!/usr/bin/env bash
# Reference certificate verifier over the public vectors in test-vectors/certificate/
# (consiliency_spec.verify_certificate; needs jsonschema and unicodedata2==16.0.0).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for path in test-vectors/certificate/*.json test-vectors/certificate/*/*.json; do
  python3 -m json.tool "$path" >/dev/null
done

PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 tests/test_certificate_vectors.py
