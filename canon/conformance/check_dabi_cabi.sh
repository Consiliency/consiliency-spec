#!/usr/bin/env bash
# DABI conformance gate — the canon-core C ABI (`c-binding` feature) is byte-identical to the engine.
#
# Builds canon-core with the `c-binding` feature, regenerates the cbindgen header and asserts it does
# NOT drift from the checked-in copy (deterministic generation), then compiles + runs the C smoke test
# against the built cdylib. The smoke test round-trips a corpus vector through the extern "C" surface
# and asserts the canonical bytes + digest match the pinned corpus, plus that a reject vector
# (lone surrogate) surfaces CANON_ERR (fail-loud, never a silent pass).
#
# This is additive: it exercises the NEW C ABI only. It touches no existing engine, corpus, binding,
# or gate — all pre-existing canon gates stay green (the c-binding feature is off by default).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bash "$ROOT/canon/core/scripts/check_cabi_smoke.sh"
