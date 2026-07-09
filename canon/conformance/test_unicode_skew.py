"""Unicode-skew negative tests for the canon v2 INGEST boundary (SPEC.md section 5).

canon v2 no longer normalizes NFC inside the hash; NFC relocated to the ingestion boundary
(canon/py/canon_ingest.py), which now owns the pinned Unicode DB (unicodedata2 16.0) and the fail-closed
version assertion. So the two things the cross-language byte-identity gate CANNOT prove on its own
now target `ingest`, not `canon`:

  1. The fail-closed version assertion ACTUALLY fails on a wrong Unicode version (a simulated
     mismatch must be loud, never silent) — the assertion lives in ingest.py now.
  2. The shipped post-13 inputs are LIVE discriminators: their NFC under a Unicode-13 DB (stdlib
     `unicodedata`) byte-differs from their NFC under the pinned Unicode-16 DB (unicodedata2). This
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


def test_post13_inputs_are_live_discriminators() -> None:
    """Each post13 input must NFC-diverge between stdlib and the pinned DB *on hosts where the skew
    exists for that input*.

    The inputs mix marks assigned in different Unicode versions (U+0C3C, U+1715 are U15.0; U+0897 is
    U16.0). Whether a given input is a live discriminator depends on what THIS host's stdlib
    `unicodedata` knows: a CPython shipping Unicode 15.x already classifies the U15 marks correctly,
    so those inputs will NOT diverge there — and that is correct, not a failure. So per input we
    require divergence IFF stdlib under-classifies (disagrees on ccc for) at least one of its marks
    vs the pinned DB. That keeps the proof host-robust (works on 3.10..current) while still proving
    the pin is load-bearing wherever a real skew is present. The production ingest path (ingest.py
    always using unicodedata2) is host-independent regardless; this only validates the proof inputs.
    """
    import unicodedata as stdlib_unicodedata  # host CPython DB (version varies by CPython build)
    pinned = ingest.unicodedata  # unicodedata2 16.0.0

    stdlib_ver = ingest._unicode_major_minor(stdlib_unicodedata.unidata_version)
    pinned_ver = ingest._unicode_major_minor(pinned.unidata_version)
    print("  stdlib unicodedata=%s  pinned(unicodedata2)=%s" % (stdlib_ver, pinned_ver))

    with open(VECTORS, encoding="utf-8") as f:
        vectors = json.load(f)
    post13 = [v for v in vectors if v["name"].startswith("post13-")]
    assert post13, "no post13-* inputs found in canon-vectors.json"

    failures = []
    exercised = 0
    for v in post13:
        s = canon.decode_input(v["input"])
        assert isinstance(s, str), "post13 input %r is not a string input" % v["name"]
        # Does stdlib under-classify any codepoint in this input (ccc disagreement with pinned)?
        stdlib_underclassifies = any(
            stdlib_unicodedata.combining(ch) != pinned.combining(ch) for ch in s
        )
        nfc_stdlib = stdlib_unicodedata.normalize("NFC", s).encode("utf-8")
        nfc_pinned = ingest.normalize_string(s).encode("utf-8")
        diverges = nfc_stdlib != nfc_pinned

        if stdlib_underclassifies:
            exercised += 1
            if diverges:
                print(
                    "  [ok] %s: NFC@%s=%s differs from ingest NFC@%s=%s (live discriminator)"
                    % (v["name"], stdlib_ver, nfc_stdlib.hex(), pinned_ver, nfc_pinned.hex())
                )
            else:
                failures.append(
                    "%s: stdlib under-classifies a mark yet ingest NFC is IDENTICAL across DBs; "
                    "input is not actually discriminating" % v["name"]
                )
        else:
            # stdlib already knows every mark in this input -> no skew to exercise on this host.
            print(
                "  [skip] %s: stdlib(%s) already classifies all marks; no skew on this host"
                % (v["name"], stdlib_ver)
            )

    if failures:
        for f in failures:
            print("  [FAIL] " + f)
        raise AssertionError("post13 inputs are not live discriminators where a skew exists")
    if exercised == 0:
        # Every input's marks are known to this host's stdlib (e.g. CPython shipping Unicode >=16):
        # there is genuinely no DB skew to discriminate here. Say so; do not silently "pass".
        print(
            "  [note] no post13 input exercised a skew on this host (stdlib=%s); the pin is still "
            "enforced by the import-time assertion in ingest.py." % stdlib_ver
        )


def main() -> int:
    print("== ingest Unicode-skew negative tests ==")
    test_assertion_fails_on_wrong_version()
    test_post13_inputs_are_live_discriminators()
    print("INGEST UNICODE-SKEW NEGATIVE TESTS: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
