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

    ``raw`` vectors are JSON text (e.g. nesting past every port's cap); ``input`` vectors are tagged trees.
    Only CanonError counts as a rejection; any other exception propagates as a real failure.
    """
    try:
        node = json.loads(bv["raw"]) if "raw" in bv else bv["input"]
    except RecursionError:
        # The stdlib parser recurses per level (it fails near 10,000 on 3.12). Raw text that deep is
        # a rejection, never an uncaught crash of the self-test.
        return ("ERROR", "-", "-")
    try:
        value = canon.decode_input(node)
        cbytes = canon.canonical_bytes(value)
    except canon.CanonError:
        return ("ERROR", "-", "-")
    return ("OK", base64.b64encode(cbytes).decode("ascii"), canon.digest(value, "semantic-content"))


def emit_boundary(path=BOUNDARY):
    lines = ["%s\t%s\t%s\t%s" % ((bv["name"],) + run_boundary(bv)) for bv in load(path)]
    sys.stdout.write("\n".join(sorted(lines)) + "\n")


def boundary_failures(path=BOUNDARY):
    failures = []
    for bv in load(path):
        status, _, _ = run_boundary(bv)
        if bv["expect"] == "reject":
            if status != "ERROR":
                failures.append("boundary %s: expected CanonError, accepted" % bv["name"])
            continue
        if status != "OK":
            failures.append("boundary %s: expected acceptance, rejected" % bv["name"])
            continue
        if isinstance(bv.get("bytes"), str):
            got = canon.canonical_bytes(canon.decode_input(bv["input"])).decode("utf-8")
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
    return failures


def test():
    vectors = load()
    failures = boundary_failures() + native_failures()
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
    print("PYTHON CONFORMANCE: PASS (%d vectors, %d boundary vectors, native depth cap) "
          "[canon v2: Unicode-DB-independent]" % (len(vectors), len(load(BOUNDARY))))
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
