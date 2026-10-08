#!/usr/bin/env python3
"""Emit canon output through the BUILT PyO3 binding (`canon_core`), not the Python reference.

The XG4 gate's [4/5] step used to only ``cargo check`` the PyO3 surface — it proved the binding
COMPILES, never that the built ``.so`` a Python consumer imports emits the SAME bytes/digest as the
reference. This harness closes that gap: it imports the compiled ``canon_core`` extension module
(the real artifact) and emits one TAB-separated line per vector — ``name\tbytes_b64\tdigest`` sorted
by name, ``ERROR\tERROR`` for reject vectors — byte-identical in format to ``py/test_canon.py --emit``
and ``ts/canon.test.ts --emit`` so the gate can ``diff`` them directly.

Usage:
    PYTHONPATH=<dir-with-canon_core.so> python3 emit_pyo3.py <corpus.json>
    PYTHONPATH=<dir-with-canon_core.so> python3 emit_pyo3.py --boundary <engine_boundary_vectors.json>

``--boundary`` emits ``name\tOK|ERROR\tbytes_b64\tdigest`` (``-\t-`` on rejection), byte-identical in
format to ``py/test_canon.py --emit-boundary``, for XG4's five-engine boundary diff.

Only the binding's own error (``ValueError``, which PyO3 raises for every CanonError) counts as a
rejection; any other exception is a real failure and propagates (CAN-15).

The binding takes tagged JSON (``canonical_bytes_from_json`` / ``digest_from_json``); we re-serialize
each vector's ``input`` with ``ensure_ascii=True`` so a lone-surrogate ESCAPE reaches serde_json as
ASCII (which rejects it at parse — matching the reference), exactly as the corpus stores it.
"""
from __future__ import annotations

import base64
import json
import sys

import canon_core  # the BUILT PyO3 extension module (from PYTHONPATH); import failure = gate fail


def run_vector(vec):
    tagged_json = json.dumps(vec["input"], ensure_ascii=True)
    if vec.get("expect_error"):
        try:
            canon_core.canonical_bytes_from_json(tagged_json)
        except ValueError:
            return ("ERROR", "ERROR")
        raise SystemExit(f"vector {vec['name']}: expected CanonError but PyO3 binding accepted it")
    cbytes = canon_core.canonical_bytes_from_json(tagged_json)
    b64 = base64.b64encode(bytes(cbytes)).decode("ascii")
    dig = canon_core.digest_from_json(tagged_json, vec["profile"])
    return (b64, dig)


def run_boundary(bv):
    tagged_json = bv["raw"] if "raw" in bv else json.dumps(bv["input"], ensure_ascii=True)
    try:
        cbytes = canon_core.canonical_bytes_from_json(tagged_json)
    except ValueError:
        return ("ERROR", "-", "-")
    b64 = base64.b64encode(bytes(cbytes)).decode("ascii")
    return ("OK", b64, canon_core.digest_from_json(tagged_json, "semantic-content"))


def main() -> int:
    if sys.argv[1:2] == ["--boundary"]:
        with open(sys.argv[2], encoding="utf-8") as handle:
            boundary = json.load(handle)
        print("\n".join(sorted("\t".join((bv["name"],) + run_boundary(bv)) for bv in boundary)))
        return 0
    corpus = sys.argv[1]
    with open(corpus, encoding="utf-8") as handle:
        vectors = json.load(handle)
    lines = []
    for vec in vectors:
        b64, dig = run_vector(vec)
        lines.append(f"{vec['name']}\t{b64}\t{dig}")
    lines.sort()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
