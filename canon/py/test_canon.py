"""Python conformance test: run every vector in canon-vectors.json, assert bytes + digest.

Usage:
    python3 canon/py/test_canon.py               # human-readable PASS/FAIL, exit 0/1
    python3 canon/py/test_canon.py --emit         # emit name\tbytes_b64\tdigest for x-lang diff
    python3 canon/py/test_canon.py --emit <corpus> # emit over an alternate corpus (e.g. the cross-repo
                                                   # downstream-consumer vectors); the emit is the canon v2
                                                   # REFERENCE output, used to hold every other v2 engine
                                                   # (Rust core, PyO3, WASM) byte-identical over that domain.
    python3 canon/py/test_canon.py --emit-boundary # emit name\tOK|ERROR\tbytes_b64\tdigest over the
                                                   # engine boundary vectors, for cross-engine diffs

The self-test also runs ../conformance/engine_boundary_vectors.json (CAN-13: they
used to run only against the already-published packages, so a regression in this tree was invisible)
and the native-value checks the JSON corpus cannot carry (the nesting cap on a native value).
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
BOUNDARY = os.path.join(HERE, "..", "conformance", "engine_boundary_vectors.json")
# Boundary vectors for behaviour no PUBLISHED canon-core has yet. check_published_canon_core.sh runs
# BOUNDARY against the pinned published engines; this file runs only against the in-tree engines (these
# self-tests, cargo test, XG4). Fold it into BOUNDARY when the published gate is repinned. Empty since the
# canon-core 0.4.0 repin, which folded duplicate-key-f1..f6 into BOUNDARY.
BOUNDARY_NEXT = os.path.join(HERE, "..", "conformance", "engine_boundary_vectors_next.json")

# Count guards (CAN-15): a truncated local corpus must fail, not pass on whatever is left. The
# corpus only grows (published vectors are never removed), so these are floors, raised with it.
MIN_VALID = 44
MIN_ERROR = 37
MIN_BOUNDARY = 26
MIN_BOUNDARY_NEXT = 0


def load(path=VECTORS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_vector(vec):
    """Return (bytes_b64, digest_hex) or raise; for reject vectors return the sentinel ('ERROR', 'ERROR')."""
    if vec.get("expect_error"):
        # decode_input is INSIDE the guard: a $int payload-grammar rejection is raised at decode.
        # Only CanonError counts; any other exception type is a real failure and propagates.
        try:
            canon.canonical_bytes(canon.decode_input(vec["input"]))
        except canon.CanonError:
            return ("ERROR", "ERROR")
        raise AssertionError("expected CanonError but encoding succeeded")
    value = canon.decode_input(vec["input"])
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


def run_boundary(bv):
    """Return (status, bytes_b64, digest) for one engine boundary vector; ('ERROR', '-', '-') on reject.

    ``raw`` vectors are JSON text (nesting past every port's cap, bare-number spellings) and go through
    the text entry point ``decode_input_json``, which maps the stdlib parser's RecursionError (near
    10,000 levels) to CanonError; ``input`` vectors are tagged trees. Only CanonError counts as a
    rejection; any other exception propagates as a real failure.
    """
    try:
        value = canon.decode_input_json(bv["raw"]) if "raw" in bv else canon.decode_input(bv["input"])
        cbytes = canon.canonical_bytes(value)
    except canon.CanonError:
        return ("ERROR", "-", "-")
    return ("OK", base64.b64encode(cbytes).decode("ascii"), canon.digest(value, "semantic-content"))


def emit_boundary(path=BOUNDARY):
    lines = ["%s\t%s\t%s\t%s" % ((bv["name"],) + run_boundary(bv)) for bv in load(path)]
    sys.stdout.write("\n".join(sorted(lines)) + "\n")


def boundary_failures(path=BOUNDARY, minimum=MIN_BOUNDARY):
    failures = []
    vectors = load(path)
    if len(vectors) < minimum:
        failures.append("%s has %d vectors (< %d); it looks truncated" % (os.path.basename(path), len(vectors), minimum))
    for bv in vectors:
        status, b64, _ = run_boundary(bv)
        if bv["expect"] == "reject":
            if status != "ERROR":
                failures.append("boundary %s: expected CanonError, accepted" % bv["name"])
            continue
        if status != "OK":
            failures.append("boundary %s: expected acceptance, rejected" % bv["name"])
            continue
        if isinstance(bv.get("bytes"), str):
            got = base64.b64decode(b64).decode("utf-8")
            if got != bv["bytes"]:
                failures.append("boundary %s: bytes %r != %r" % (bv["name"], got, bv["bytes"]))
    return failures


def native_failures():
    """Checks on native values that the tagged JSON corpus cannot express."""
    failures = []

    def nested(depth):
        value = []
        for _ in range(depth - 1):
            value = [value]
        return value

    # CAN-12: the cap applies to native values, not only to decoded vectors, and a value far past it is a
    # CanonError (it used to be a RecursionError near depth 250). Run from inside extra frames so the
    # check does not pass only because the test sits at the bottom of the stack.
    def at_depth(frames, fn):
        return fn() if frames == 0 else at_depth(frames - 1, fn)

    if canon.MAX_DEPTH != 128:
        failures.append("MAX_DEPTH is %r, expected 128" % canon.MAX_DEPTH)
    try:
        at_depth(150, lambda: canon.canonical_bytes(nested(canon.MAX_DEPTH)))
    except Exception as error:  # noqa: BLE001
        failures.append("native depth %d: expected acceptance, got %r" % (canon.MAX_DEPTH, error))
    for depth in (canon.MAX_DEPTH + 1, 100000):
        for label, fn in (("canonical_bytes", canon.canonical_bytes), ("decode_input", canon.decode_input),
                          ("digest", lambda v: canon.digest(v, "run"))):
            try:
                at_depth(150, lambda: fn(nested(depth)))
            except canon.CanonError:
                continue
            except Exception as error:  # noqa: BLE001
                failures.append("native %s depth %d: expected CanonError, got %r" % (label, depth, error))
                continue
            failures.append("native %s depth %d: expected CanonError, accepted" % (label, depth))

    # CAN-5: a tuple is Python's native array alias, encoded exactly like a list (SPEC.md section 1).
    if not canon.canonical_bytes((1, (2, 3))) == canon.canonical_bytes([1, [2, 3]]) == b"[1,[2,3]]":
        failures.append("tuple no longer encodes as an array")

    # D6: the tagged-JSON TEXT entry point reads bare numbers the way Rust and TypeScript do.
    for text, expected in (("9007199254740991", b"9007199254740991"), ("[-9007199254740991,0]",
                                                                       b"[-9007199254740991,0]")):
        got = canon.canonical_bytes(canon.decode_input_json(text))
        if got != expected:
            failures.append("decode_input_json(%s): %r != %r" % (text, got, expected))
    for text in ("9007199254740992", "-9007199254740992", "18446744073709551616", "1" * 5000, "-0",
                 "1.0", "1e3", "10E-1", "NaN", "Infinity", "[1,", "{\"n\": 1e3}"):
        try:
            canon.canonical_bytes(canon.decode_input_json(text))
        except canon.CanonError:
            continue
        except Exception as error:  # noqa: BLE001
            failures.append("decode_input_json(%.20s): expected CanonError, got %r" % (text, error))
            continue
        failures.append("decode_input_json(%.20s): expected CanonError, accepted" % text)
    # A parsed tree (no spelling left) applies the same range rule to native ints.
    for value in (2 ** 53, -(2 ** 53), 2 ** 64):
        try:
            canon.decode_input({"n": value})
        except canon.CanonError:
            continue
        failures.append("decode_input(bare %d): expected CanonError, accepted" % value)
    return failures


def test():
    vectors = load()
    failures = boundary_failures() + boundary_failures(BOUNDARY_NEXT, MIN_BOUNDARY_NEXT) + native_failures()
    n_error = sum(1 for vec in vectors if vec.get("expect_error"))
    if len(vectors) - n_error < MIN_VALID or n_error < MIN_ERROR:
        failures.append("corpus has %d valid / %d reject vectors (< %d / %d); it looks truncated"
                        % (len(vectors) - n_error, n_error, MIN_VALID, MIN_ERROR))
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
    print("PYTHON CONFORMANCE: PASS (%d vectors, %d+%d boundary vectors, native depth cap) "
          "[canon v2: Unicode-DB-independent]" % (len(vectors), len(load(BOUNDARY)), len(load(BOUNDARY_NEXT))))
    return 0


if __name__ == "__main__":
    if "--emit-boundary" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--emit-boundary"]
        emit_boundary(args[0] if args else BOUNDARY)
    elif "--emit" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--emit"]
        emit(args[0] if args else VECTORS)
    else:
        sys.exit(test())
