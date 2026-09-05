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
echo "== consiliency-spec public release gate GREEN =="
