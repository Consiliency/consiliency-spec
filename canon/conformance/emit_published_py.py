#!/usr/bin/env python3
"""Emit + ASSERT canon output through the PUBLISHED PyPI wheel ``consiliency-canon-core``.

This is the GATE-phase companion to emit_pyo3.py. emit_pyo3.py imports a ``.so`` BUILT FROM SOURCE in
the working tree; THIS harness imports ``canon_core`` from a wheel a Python consumer actually installs
from PyPI (into the orchestrator's venv). The published wheel is a SEPARATE code path from the source
build — a bad maturin ``include``, a stale corpus, a wrong ABI tag would be invisible to the
source-built gate but caught here.

The oracle is the corpus SHIPPED INSIDE the wheel (``site-packages/canon-vectors.json``): every valid
vector's emitted bytes(b64)+digest(hex) MUST equal that vector's ``expected_*`` fields, and every
``expect_error`` vector MUST reject. Count guards fail-closed against a truncated corpus.

It also runs the shared engine-level BOUNDARY vectors (engine_boundary_vectors.json): the tag-vs-
literal-``$int``-key razor, 2^53+/-1 exact big integers, and nesting past the serde recursion ceiling
(which MUST reject, not mis-digest).

Usage:
    python emit_published_py.py <corpus.json> <boundary.json> <out_corpus.txt> <out_boundary.txt>
    (run with the venv interpreter the orchestrator installed the wheel into.)

Exit 0 = all assertions hold. Exit 1 = a divergence (bytes/digest/accept/reject/count) — gate fail.
"""
from __future__ import annotations

import base64
import json
import sys

import canon_core  # the PUBLISHED wheel's extension module; import failure = gate fail

MIN_VALID = 30
MIN_ERROR = 6

_failures = 0


def _fail(msg: str) -> None:
    global _failures
    print(f"FAIL: {msg}", file=sys.stderr)
    _failures += 1


def main() -> int:
    corpus_path, boundary_path, out_corpus, out_boundary = sys.argv[1:5]

    # --- Corpus vectors: assert against the wheel-shipped oracle (expected_* fields) ---------------
    with open(corpus_path, encoding="utf-8") as handle:
        vectors = json.load(handle)
    valid = errors = 0
    corpus_lines = []
    for v in vectors:
        tagged = json.dumps(v["input"], ensure_ascii=True)  # lone surrogate -> \udXXX escape, rejected at parse
        if v.get("expect_error"):
            errors += 1
            try:
                canon_core.canonical_bytes_from_json(tagged)
                _fail(f"vector {v['name']}: expected rejection but the PUBLISHED wheel accepted it")
                corpus_lines.append(f"{v['name']}\tACCEPTED-BUG\tACCEPTED-BUG")
            except Exception:
                corpus_lines.append(f"{v['name']}\tERROR\tERROR")
            continue
        valid += 1
        b64 = base64.b64encode(bytes(canon_core.canonical_bytes_from_json(tagged))).decode("ascii")
        dig = canon_core.digest_from_json(tagged, v["profile"])
        if b64 != v["expected_canonical_bytes_b64"]:
            _fail(f"vector {v['name']}: bytes {b64} != oracle {v['expected_canonical_bytes_b64']}")
        if dig != v["expected_digest_hex"]:
            _fail(f"vector {v['name']}: digest {dig} != oracle {v['expected_digest_hex']}")
        corpus_lines.append(f"{v['name']}\t{b64}\t{dig}")
    if valid < MIN_VALID:
        _fail(f"only {valid} valid vectors (< {MIN_VALID}); corpus looks truncated")
    if errors < MIN_ERROR:
        _fail(f"only {errors} expect_error vectors (< {MIN_ERROR}); corpus looks truncated")
    corpus_lines.sort()
    with open(out_corpus, "w", encoding="utf-8") as handle:
        handle.write("\n".join(corpus_lines) + "\n")

    # --- Engine-level boundary vectors ------------------------------------------------------------
    with open(boundary_path, encoding="utf-8") as handle:
        boundary = json.load(handle)
    boundary_lines = []
    for bv in boundary:
        tagged = bv["raw"] if "raw" in bv else json.dumps(bv["input"], ensure_ascii=True)
        if bv["expect"] == "reject":
            try:
                canon_core.canonical_bytes_from_json(tagged)
                _fail(f"boundary {bv['name']}: expected rejection but the PUBLISHED wheel accepted it")
                boundary_lines.append(f"{bv['name']}\tACCEPTED-BUG\t-\t-")
            except Exception:
                boundary_lines.append(f"{bv['name']}\tERROR\t-\t-")
            continue
        # expect accept
        try:
            out = bytes(canon_core.canonical_bytes_from_json(tagged))
            b64 = base64.b64encode(out).decode("ascii")
            dig = canon_core.digest_from_json(tagged, "semantic-content")
            if isinstance(bv.get("bytes"), str) and out.decode("utf-8") != bv["bytes"]:
                _fail(f"boundary {bv['name']}: canonical bytes {out.decode('utf-8')!r} != expected {bv['bytes']!r}")
        except Exception as error:  # noqa: BLE001
            _fail(f"boundary {bv['name']}: expected acceptance but the PUBLISHED wheel rejected it ({str(error)[:80]})")
            b64 = dig = "REJECTED-BUG"
        boundary_lines.append(f"{bv['name']}\tOK\t{b64}\t{dig}")
    boundary_lines.sort()
    with open(out_boundary, "w", encoding="utf-8") as handle:
        handle.write("\n".join(boundary_lines) + "\n")

    print(
        f"PyPI consiliency-canon-core :: corpus valid={valid} error={errors}, "
        f"boundary={len(boundary)}, failures={_failures}"
    )
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
