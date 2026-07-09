"""Python conformance test: run every vector in canon-vectors.json, assert bytes + digest.

Usage:
    python3 canon/py/test_canon.py               # human-readable PASS/FAIL, exit 0/1
    python3 canon/py/test_canon.py --emit         # emit name\tbytes_b64\tdigest for x-lang diff
    python3 canon/py/test_canon.py --emit <corpus> # emit over an alternate corpus (e.g. the cross-repo
                                                   # downstream-consumer vectors); the emit is the canon v2
                                                   # REFERENCE output, used to hold every other v2 engine
                                                   # (Rust core, PyO3, WASM) byte-identical over that domain.
"""

import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import canon  # noqa: E402

# canon v2 is Unicode-DB-INDEPENDENT (NFC moved to the ingestion boundary), so there is no Unicode
# version to report here. The pinned-DB / skew coverage lives in canon/conformance/test_unicode_skew
# .py + test_ingest_nfc.py, which target the ingest module.

VECTORS = os.path.join(HERE, "..", "vectors", "canon-vectors.json")


def load(path=VECTORS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_vector(vec):
    """Return (bytes_b64, digest_hex) or raise; for reject vectors return the sentinel ('ERROR', 'ERROR')."""
    value = canon.decode_input(vec["input"])
    if vec.get("expect_error"):
        try:
            canon.canonical_bytes(value)
        except canon.CanonError:
            return ("ERROR", "ERROR")
        raise AssertionError("expected CanonError but encoding succeeded")
    cbytes = canon.canonical_bytes(value)
    return (base64.b64encode(cbytes).decode("ascii"), canon.digest(value, vec["profile"]))


def emit(path=VECTORS):
    """Emit one TAB-separated line per vector (name, bytes_b64, digest), sorted by name.

    Plain string concatenation only -- NO json.dumps -- so the cross-language diff in check.sh
    compares canon output itself, never an incidental JSON-pretty-printing difference between
    Python and JS (which is exactly the divergence canon exists to prevent).
    """
    lines = []
    for vec in load(path):
        b64, dig = run_vector(vec)
        lines.append("%s\t%s\t%s" % (vec["name"], b64, dig))
    sys.stdout.write("\n".join(sorted(lines)) + "\n")


def test():
    vectors = load()
    failures = []
    for vec in vectors:
        name = vec["name"]
        b64, dig = run_vector(vec)
        if vec.get("expect_error"):
            if (b64, dig) != ("ERROR", "ERROR"):
                failures.append("%s: expected error, got %s" % (name, b64))
            continue
        if b64 != vec["expected_canonical_bytes_b64"]:
            failures.append("%s: bytes mismatch\n  expected %s\n  got      %s"
                            % (name, vec["expected_canonical_bytes_b64"], b64))
        if dig != vec["expected_digest_hex"]:
            failures.append("%s: digest mismatch\n  expected %s\n  got      %s"
                            % (name, vec["expected_digest_hex"], dig))
    if failures:
        print("PYTHON CONFORMANCE: FAIL (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("PYTHON CONFORMANCE: PASS (%d vectors) [canon v2: Unicode-DB-independent]"
          % len(vectors))
    return 0


if __name__ == "__main__":
    if "--emit" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--emit"]
        emit(args[0] if args else VECTORS)
    else:
        sys.exit(test())
