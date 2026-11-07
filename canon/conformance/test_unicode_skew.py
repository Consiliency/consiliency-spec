"""Unicode-skew negative tests for the canon v2 INGEST boundary (SPEC.md section 5).

canon v2 no longer normalizes NFC inside the hash; NFC relocated to the ingestion boundary
(canon/py/canon_ingest.py), which now owns the pinned Unicode DB (unicodedata2 16.0) and the fail-closed
version assertion. So the two things the cross-language byte-identity gate CANNOT prove on its own
now target `ingest`, not `canon`:

  1. The fail-closed version assertion ACTUALLY fails on a wrong Unicode version (a simulated
     mismatch must be loud, never silent) — the assertion lives in ingest.py now.
  2. The shipped post-13 inputs are LIVE discriminators: their NFC under a DB that predates their
     marks (simulated, so the proof holds on every host) byte-differs from their NFC under the
     pinned Unicode-16 DB (unicodedata2). This
     is the concrete evidence that a DB skew would break ingest determinism — and it is the ONLY
     thing that proves the pin is load-bearing, because canon v2 passes bytes through verbatim and
     the cross-language gate stays green even if ingest NFC were missing entirely.

Run: PYTHONPATH=src python3 canon/conformance/test_unicode_skew.py   (exit 0 = pass)
"""

import hashlib
import json
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(HERE, "..", "vectors", "canon-vectors.json")
# The spec_core package (src/spec_core/canon, byte-mirrored at canon/py): run with src/ as the import
# root, which canon/conformance/check.sh exports.
SRC = os.path.normpath(os.path.join(HERE, "..", "..", "src"))

from spec_core.canon import canon  # canon v2: Unicode-DB-independent; only decodes vector inputs
from spec_core.canon import canon_ingest as ingest  # asserts the pinned DB at import; must succeed


def test_assertion_fails_on_wrong_version() -> None:
    """Simulated mismatch: import ingest in a subprocess where unicodedata2 reports a WRONG version.

    We shadow the real unicodedata2 with a stub module that reports unidata_version='13.0.0'. The
    ingest boundary's import-time, fail-closed assertion (EXPECTED_UNICODE='16.0' != '13.0') MUST
    raise, so the import must fail with a non-zero exit. This exercises the REAL guard in ingest.py,
    not a re-implemented copy. A clean exit here would mean the divergence guard is dead — the exact
    silent-failure mode this whole change exists to prevent.
    """
    import tempfile

    # A fresh directory per call, outside the source tree: a fixed path shared by parallel runs raced
    # (one run's cleanup removed the stub another was about to import).
    stub_dir = tempfile.mkdtemp(prefix="skew-stub-")
    stub_path = os.path.join(stub_dir, "unicodedata2.py")
    try:
        with open(stub_path, "w", encoding="utf-8") as f:
            # Minimal stub: a stale unidata_version + a passthrough normalize so import gets far
            # enough to hit the version assertion (it runs before any normalize call).
            f.write(
                "import unicodedata as _u\n"
                "unidata_version = '13.0.0'\n"
                "def normalize(form, s):\n"
                "    return _u.normalize(form, s)\n"
            )
        # Put the stub dir FIRST on PYTHONPATH so it shadows the real unicodedata2; add src so
        # `spec_core.canon.canon_ingest` resolves. Hermetic: `-P`
        # keeps the child's working directory off sys.path (a `-c` child would otherwise put it at
        # sys.path[0], AHEAD of the stub), and only absolute inherited PYTHONPATH entries are kept: an
        # empty or relative entry (such as ".") also resolves against the working directory. So no file
        # in whatever directory the test runs from (a unicodedata2.py, a sitecustomize.py) can stand in
        # for the stub through the working directory. An inherited ABSOLUTE entry is kept as given, by
        # design, even when it names the working directory or a symlink to it: the caller chose that
        # path. Such an entry sits behind the stub and src, but a sitecustomize.py in it still runs at
        # child startup and can pre-seed unicodedata2 ahead of the stub, so it bypasses this test.
        env = dict(os.environ)
        inherited = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if os.path.isabs(p)]
        env["PYTHONPATH"] = os.pathsep.join([stub_dir, SRC] + inherited)
        code = textwrap.dedent(
            """
            from spec_core.canon import canon_ingest  # noqa: F401  -- must RAISE (stale Unicode)
            print("LOADED-WITHOUT-ASSERTION")  # reaching here is a FAILURE
            """
        )
        proc = subprocess.run(
            [sys.executable, "-P", "-c", code], env=env, capture_output=True, text=True
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode != 0, (
            "ingest imported cleanly under a simulated stale (13.0) Unicode DB; the fail-closed "
            "version assertion is NOT firing. Output:\n" + combined
        )
        assert "Unicode DB mismatch" in combined, (
            "ingest failed to import but NOT via the Unicode version assertion; got:\n" + combined
        )
        print("  [ok] ingest version assertion fails loudly on a simulated Unicode-version mismatch")
    finally:
        # Remove the whole stub tree (the subprocess may have left a __pycache__ behind).
        import shutil

        shutil.rmtree(stub_dir, ignore_errors=True)


def test_assertion_child_ignores_the_working_directory() -> None:
    """The stale-version child is independent of the directory the test runs from.

    Run the version-assertion test from a directory holding a `unicodedata2.py` that reports the
    PINNED version (16.0) and a `sitecustomize.py` that pre-seeds one, first with a trailing empty
    PYTHONPATH entry and then with a relative "." entry. If the child let the working directory onto
    sys.path, it would pick up an impostor, ingest would load cleanly, and
    test_assertion_fails_on_wrong_version would fail.
    """
    import shutil
    import tempfile

    work = tempfile.mkdtemp(prefix="skew-cwd-")
    old_cwd, old_pp = os.getcwd(), os.environ.get("PYTHONPATH")
    try:
        with open(os.path.join(work, "unicodedata2.py"), "w", encoding="utf-8") as f:
            f.write(
                "import unicodedata as _u\n"
                "unidata_version = '16.0.0'\n"
                "def normalize(form, s):\n"
                "    return _u.normalize(form, s)\n"
            )
        # A relative "." entry also names the working directory. A sitecustomize.py there would run at
        # child startup and pre-seed the module cache ahead of the stub.
        with open(os.path.join(work, "sitecustomize.py"), "w", encoding="utf-8") as f:
            f.write(
                "import sys, types, unicodedata as _u\n"
                "_m = types.ModuleType('unicodedata2')\n"
                "_m.unidata_version = '16.0.0'\n"
                "_m.normalize = _u.normalize\n"
                "sys.modules['unicodedata2'] = _m\n"
            )
        os.chdir(work)
        for extra in ("", "."):  # a trailing empty entry, then a relative "." entry: both the cwd
            os.environ["PYTHONPATH"] = (old_pp or "") + os.pathsep + extra
            test_assertion_fails_on_wrong_version()
        print("  [ok] the stale-version child ignores the working directory (empty and '.' entries)")
    finally:
        os.chdir(old_cwd)
        if old_pp is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old_pp
        shutil.rmtree(work, ignore_errors=True)


def _ingest_child(prelude: str, stub_dir: str = ""):
    """Import canon_ingest in a hermetic child after `prelude` has run; return (rc, stdout+stderr).

    The child sees only `stub_dir` (when given) and src/ on its import path: `-P` keeps its working
    directory off sys.path and the inherited PYTHONPATH is replaced, so nothing outside the prelude and
    the stub directory can supply a Unicode database.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([p for p in (stub_dir, SRC) if p])
    code = prelude + textwrap.dedent(
        """
        from spec_core.canon import canon_ingest
        print("SOURCE=%s VERSION=%s" % (canon_ingest.UNICODE_DB_SOURCE,
                                        canon_ingest.unicodedata.unidata_version))
        """
    )
    proc = subprocess.run([sys.executable, "-P", "-c", code], env=env, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# The packages leave unicodedata2 out on CPython 3.14 (marker "python_version != '3.14' or
# platform_python_implementation != 'CPython'"), whose stdlib reports Unicode 16.0.0. Without the
# backport, ingest must use the stdlib only when it reports the pinned version, and fail closed otherwise.
_NO_BACKPORT = "import sys\nsys.modules['unicodedata2'] = None  # the backport is not installed\n"
_STDLIB_REPORTS = (
    "import types, unicodedata as _real\n"
    "_stub = types.ModuleType('unicodedata')\n"
    "_stub.unidata_version = %r\n"
    "_stub.normalize = _real.normalize\n"
    "sys.modules['unicodedata'] = _stub\n"
)


def test_stdlib_fallback_only_at_the_pinned_version() -> None:
    """Without the backport, a stdlib reporting 16.0 is used and any other stdlib version refuses."""
    rc, out = _ingest_child(_NO_BACKPORT + _STDLIB_REPORTS % "16.0.0")
    assert rc == 0 and "SOURCE=stdlib unicodedata VERSION=16.0.0" in out, (
        "without unicodedata2, a stdlib reporting 16.0.0 must be used; got rc=%d:\n%s" % (rc, out))
    for stale in ("15.0.0", "15.1.0", "17.0.0"):
        rc, out = _ingest_child(_NO_BACKPORT + _STDLIB_REPORTS % stale)
        assert rc != 0 and "needs a Unicode 16.0 database" in out, (
            "without unicodedata2, a stdlib reporting %s must refuse to load; got rc=%d:\n%s"
            % (stale, rc, out))
    # A backport that is installed but broken (its import fails on a module of another name) is never
    # masked by the stdlib: only the backport's own absence selects the fallback.
    import shutil
    import tempfile

    stub_dir = tempfile.mkdtemp(prefix="skew-broken-")
    try:
        with open(os.path.join(stub_dir, "unicodedata2.py"), "w", encoding="utf-8") as f:
            f.write("raise ModuleNotFoundError(\"No module named '_ud2_impl'\", name='_ud2_impl')\n")
        rc, out = _ingest_child("", stub_dir)
    finally:
        shutil.rmtree(stub_dir, ignore_errors=True)
    assert rc != 0 and "_ud2_impl" in out and "SOURCE=" not in out, (
        "a broken unicodedata2 must not fall back to the stdlib; got rc=%d:\n%s" % (rc, out))
    # And the real host: with no backport, this interpreter's stdlib either loads (it reports 16.0)
    # or refuses; it never loads under another version.
    import unicodedata as host
    rc, out = _ingest_child(_NO_BACKPORT)
    if ingest._unicode_major_minor(host.unidata_version) == ingest.EXPECTED_UNICODE:
        assert rc == 0 and "SOURCE=stdlib unicodedata" in out, out
    else:
        assert rc != 0 and "needs a Unicode 16.0 database" in out, out
    print("  [ok] without unicodedata2 the stdlib is used only at Unicode 16.0 (host stdlib %s)"
          % host.unidata_version)


# The ingest-relevant Unicode 16.0 table of the unicodedata2 16.0.0 backport, pinned as one digest so the
# database ingest ACTUALLY loads is held to the backport even where the backport is not installed (CI on
# CPython 3.14 runs the stdlib path with no backport anywhere). Generator:
#   PYTHONPATH=src python3 canon/conformance/test_unicode_skew.py --print-table-digest
# which reads ONLY the backport and refuses without it. Rows per code point (surrogates excluded): the
# canonical combining class and the UTF-8 hex of NFC, NFD, NFKC, NFKD and NFC(NFD).
UNICODE16_TABLE_SHA256 = "cf605bfde7c36aa5c7a99a882ded7cdf51bf6fb6249c0db61a847b71b66de741"


def _table_digest(db) -> str:
    h = hashlib.sha256()
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        nfd = db.normalize("NFD", ch)
        row = [b"%X" % cp, b"%d" % db.combining(ch)] + [
            db.normalize(form, ch).encode("utf-8").hex().encode()
            for form in ("NFC", "NFD", "NFKC", "NFKD")] + [db.normalize("NFC", nfd).encode("utf-8").hex().encode()]
        h.update(b":".join(row) + b"\n")
    return h.hexdigest()


def test_loaded_database_matches_the_pinned_backport_table() -> None:
    """The database canon_ingest loaded (backport or 16.0 stdlib) reproduces the pinned table of the
    unicodedata2 16.0.0 backport exactly, so ingest NFC is the backport's on every host it loads on."""
    got = _table_digest(ingest.unicodedata)
    assert got == UNICODE16_TABLE_SHA256, (
        "ingest's %s %s normalization table %s != the pinned unicodedata2 16.0.0 table %s"
        % (ingest.UNICODE_DB_SOURCE, ingest.unicodedata.unidata_version, got, UNICODE16_TABLE_SHA256))
    print("  [ok] loaded DB (%s %s) == pinned unicodedata2 16.0.0 table %s... on every code point"
          % (ingest.UNICODE_DB_SOURCE, ingest.unicodedata.unidata_version, got[:16]))


def test_stdlib_and_backport_agree_when_both_are_16() -> None:
    """Where the host stdlib and the backport both report 16.0 (CPython 3.14 with unicodedata2), the
    two databases must give byte-identical normalization for every code point, so a 3.14 install
    without the backport normalizes exactly as one with it. Skipped (with a note) on any other host."""
    import unicodedata as stdlib
    try:
        import unicodedata2 as backport
    except ModuleNotFoundError as exc:
        if exc.name != "unicodedata2":
            raise
        print("  [skip] unicodedata2 not installed; stdlib/backport agreement not checked here")
        return
    if (ingest._unicode_major_minor(stdlib.unidata_version) != ingest.EXPECTED_UNICODE
            or ingest._unicode_major_minor(backport.unidata_version) != ingest.EXPECTED_UNICODE):
        print("  [skip] stdlib %s / unicodedata2 %s: not both at %s"
              % (stdlib.unidata_version, backport.unidata_version, ingest.EXPECTED_UNICODE))
        return
    diffs = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        for prop in ("combining", "category", "bidirectional", "east_asian_width", "mirrored"):
            if getattr(stdlib, prop)(ch) != getattr(backport, prop)(ch):
                diffs.append("U+%04X %s" % (cp, prop))
        if 0xAC00 <= cp <= 0xD7A3:
            # Precomposed Hangul syllables decompose algorithmically (The Unicode Standard, section 3.12). CPython 3.14's
            # decomposition() reports that arithmetic decomposition and unicodedata2 reports "";
            # the property is a reporting difference only (NFC/NFD of these agree, checked below), and
            # ingest uses normalize() alone. Pin exactly that difference so any other one fails.
            s_index = cp - 0xAC00
            parts = [0x1100 + s_index // 588, 0x1161 + (s_index % 588) // 28]
            if s_index % 28:
                parts.append(0x11A7 + s_index % 28)
            if (backport.decomposition(ch) != ""
                    or stdlib.decomposition(ch) not in ("", " ".join("%04X" % p for p in parts))):
                diffs.append("U+%04X decomposition" % cp)
        elif stdlib.decomposition(ch) != backport.decomposition(ch):
            diffs.append("U+%04X decomposition" % cp)
        for form in ("NFC", "NFD", "NFKC", "NFKD"):
            if stdlib.normalize(form, ch) != backport.normalize(form, ch):
                diffs.append("U+%04X %s" % (cp, form))
        nfd = backport.normalize("NFD", ch)
        if stdlib.normalize("NFC", nfd) != backport.normalize("NFC", nfd):  # composition + exclusions
            diffs.append("U+%04X NFC(NFD)" % cp)
    def strings(node):  # every key and string value in a vector, valid or not
        if isinstance(node, str):
            yield node
        elif isinstance(node, list):
            for item in node:
                yield from strings(item)
        elif isinstance(node, dict):
            for key, item in node.items():
                yield key
                yield from strings(item)

    with open(VECTORS, encoding="utf-8") as f:
        for v in json.load(f):
            for s in strings(v):
                for form in ("NFC", "NFD"):
                    if stdlib.normalize(form, s) != backport.normalize(form, s):
                        diffs.append("vector %s %s" % (v["name"], form))
    assert not diffs, "stdlib and unicodedata2 16.0 disagree: %s" % diffs[:20]
    print("  [ok] stdlib unicodedata %s == unicodedata2 %s on every code point (6 properties, Hangul "
          "syllable decomposition() reporting aside; 4 forms; NFC of each NFD) and every vector input" % (stdlib.unidata_version, backport.unidata_version))


# CAN-6: the discriminator proof must not depend on the host. It used to compare the pinned DB with
# the host's stdlib `unicodedata`, skipping every input whose marks the stdlib already knew: 1 of 3
# inputs was live on a Unicode-15 CPython (3.12, the CI interpreter) and 0 of 3 on any Unicode-16
# stdlib, where it passed with only a note. It now compares the pinned DB with a SIMULATED stale DB.
#
# The code points each post13 input relies on, with the Unicode version that assigned them. A DB that
# predates a code point treats it as unassigned: canonical combining class 0 and no decomposition.
POST13_MARKS = {
    0x0C3C: "15.0",  # TELUGU SIGN NUKTA (ccc 7)
    0x1715: "15.0",  # TAGALOG SIGN PAMUDPOD (ccc 9)
    0x0897: "16.0",  # ARABIC PEPET (ccc 230)
}

# NFC of each input under the pinned Unicode-16 DB, frozen as UTF-8 hex: the marks reorder by ccc.
PINNED_NFC_HEX = {
    "post13-telugu-nukta-verbatim": "e0b095e0b0bce0a591",   # KA, NUKTA(7), U+0951(230)
    "post13-arabic-pepet-verbatim": "d8a8d99ce0a297",        # BEH, U+065C(220), PEPET(230)
    "post13-two-new-marks-verbatim": "e0b0bce19c95",         # NUKTA(7), PAMUDPOD(9)
}


def _canonical_order(s: str, ccc) -> str:
    """Unicode canonical ordering (UAX #15): stably sort each run of non-starters by ccc.

    The post13 inputs hold no decomposable or composable characters (asserted below), so NFC of each
    reduces to exactly this reordering, which makes the simulated stale DB exact rather than approximate.
    """
    out, run = [], []
    for ch in s:
        if ccc(ch) == 0:
            out.extend(sorted(run, key=ccc))
            run = []
            out.append(ch)
        else:
            run.append(ch)
    out.extend(sorted(run, key=ccc))
    return "".join(out)


def test_post13_inputs_are_live_discriminators() -> None:
    """Every post13 input must NFC-differ between the pinned DB and a DB that predates its marks.

    Host-independent: the stale side is SIMULATED from the pinned DB by treating POST13_MARKS as
    unassigned (ccc 0), and the pinned side is held to a frozen literal as well as to the live
    `unicodedata2` call. All three inputs are live discriminators on every host. The host stdlib
    comparison is kept as an informational line only.
    """
    import unicodedata as stdlib_unicodedata  # host CPython DB (version varies by CPython build)
    pinned = ingest.unicodedata  # unicodedata2 16.0.0, or a 16.0 stdlib where the backport is absent

    stdlib_ver = ingest._unicode_major_minor(stdlib_unicodedata.unidata_version)
    pinned_ver = ingest._unicode_major_minor(pinned.unidata_version)
    print("  stdlib unicodedata=%s  pinned(%s)=%s" % (stdlib_ver, ingest.UNICODE_DB_SOURCE, pinned_ver))

    def stale_ccc(ch):
        return 0 if ord(ch) in POST13_MARKS else pinned.combining(ch)

    with open(VECTORS, encoding="utf-8") as f:
        vectors = json.load(f)
    post13 = [v for v in vectors if v["name"].startswith("post13-")]
    assert sorted(v["name"] for v in post13) == sorted(PINNED_NFC_HEX), (
        "post13-* inputs changed; update PINNED_NFC_HEX / POST13_MARKS")

    failures = []
    for v in post13:
        s = canon.decode_input(v["input"])
        assert isinstance(s, str), "post13 input %r is not a string input" % v["name"]
        assert any(ord(ch) in POST13_MARKS for ch in s), "%s uses no post-13 mark" % v["name"]
        for ch in s:  # the reduction NFC == canonical ordering holds only for these characters
            assert pinned.decomposition(ch) == "", (v["name"], hex(ord(ch)))
        nfc_pinned = ingest.normalize_string(s)
        nfc_stale = _canonical_order(s, stale_ccc)
        if nfc_pinned.encode("utf-8").hex() != PINNED_NFC_HEX[v["name"]]:
            failures.append("%s: pinned NFC %s != frozen %s" % (
                v["name"], nfc_pinned.encode("utf-8").hex(), PINNED_NFC_HEX[v["name"]]))
        if _canonical_order(s, pinned.combining) != nfc_pinned:
            failures.append("%s: canonical ordering under the pinned ccc != pinned NFC; the "
                            "simulation does not model this input" % v["name"])
        if nfc_stale == nfc_pinned:
            failures.append("%s: NFC is identical under a DB without its marks; the input is not "
                            "a discriminator" % v["name"])
            continue
        print("  [ok] %s: stale-DB NFC=%s, pinned NFC@%s=%s (live discriminator)"
              % (v["name"], nfc_stale.encode("utf-8").hex(), pinned_ver, nfc_pinned.encode("utf-8").hex()))
        # Informational: does THIS host's stdlib already know the marks?
        host = "differs from" if stdlib_unicodedata.normalize("NFC", s) != nfc_pinned else "matches"
        print("       (info) host stdlib NFC@%s %s the pinned DB" % (stdlib_ver, host))

    if failures:
        for f in failures:
            print("  [FAIL] " + f)
        raise AssertionError("post13 inputs are not live discriminators")


def main() -> int:
    print("== ingest Unicode-skew negative tests ==")
    test_assertion_fails_on_wrong_version()
    test_assertion_child_ignores_the_working_directory()
    test_stdlib_fallback_only_at_the_pinned_version()
    test_stdlib_and_backport_agree_when_both_are_16()
    test_loaded_database_matches_the_pinned_backport_table()
    test_post13_inputs_are_live_discriminators()
    print("INGEST UNICODE-SKEW NEGATIVE TESTS: PASS")
    return 0


def _print_table_digest() -> int:
    """Generator for UNICODE16_TABLE_SHA256: the backport's table only; refuses without the backport."""
    import unicodedata2
    if unicodedata2.unidata_version != "16.0.0":
        raise SystemExit("unicodedata2 %s is not 16.0.0" % unicodedata2.unidata_version)
    print(_table_digest(unicodedata2))
    return 0


if __name__ == "__main__":
    sys.exit(_print_table_digest() if sys.argv[1:] == ["--print-table-digest"] else main())
