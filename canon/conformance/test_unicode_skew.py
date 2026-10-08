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

Run: python3 canon/conformance/test_unicode_skew.py   (exit 0 = pass)
"""

import json
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "..", "py")
VECTORS = os.path.join(HERE, "..", "vectors", "canon-vectors.json")
sys.path.insert(0, PY)

import canon  # noqa: E402  (canon v2: Unicode-DB-independent; used only to decode vector inputs)
import canon_ingest as ingest  # noqa: E402  (asserts the pinned DB at import; must succeed here)


def test_assertion_fails_on_wrong_version() -> None:
    """Simulated mismatch: import ingest in a subprocess where unicodedata2 reports a WRONG version.

    We shadow the real unicodedata2 with a stub module that reports unidata_version='13.0.0'. The
    ingest boundary's import-time, fail-closed assertion (EXPECTED_UNICODE='16.0' != '13.0') MUST
    raise, so the import must fail with a non-zero exit. This exercises the REAL guard in ingest.py,
    not a re-implemented copy. A clean exit here would mean the divergence guard is dead — the exact
    silent-failure mode this whole change exists to prevent.
    """
    stub_dir = os.path.join(HERE, "_skew_stub")
    os.makedirs(stub_dir, exist_ok=True)
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
        # Put the stub dir FIRST on PYTHONPATH so it shadows the real unicodedata2; add canon/py so
        # `import canon_ingest` resolves.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([stub_dir, PY, env.get("PYTHONPATH", "")])
        code = textwrap.dedent(
            """
            import canon_ingest  # noqa: F401  -- must RAISE under the stubbed stale Unicode version
            print("LOADED-WITHOUT-ASSERTION")  # reaching here is a FAILURE
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True
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
    pinned = ingest.unicodedata  # unicodedata2 16.0.0

    stdlib_ver = ingest._unicode_major_minor(stdlib_unicodedata.unidata_version)
    pinned_ver = ingest._unicode_major_minor(pinned.unidata_version)
    print("  stdlib unicodedata=%s  pinned(unicodedata2)=%s" % (stdlib_ver, pinned_ver))

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
    test_post13_inputs_are_live_discriminators()
    print("INGEST UNICODE-SKEW NEGATIVE TESTS: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
