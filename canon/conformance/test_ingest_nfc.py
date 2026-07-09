"""canon v2 INGEST-NFC enforcement test (SPEC.md section 5).

THE blind-spot guard. canon v2 emits bytes verbatim (it no longer normalizes), so the
cross-language byte-identity gate (check.sh) stays GREEN even if ingest NFC were missing entirely —
both ports would just pass the same non-NFC bytes through. The ONLY thing that proves NFC
enforcement actually lives at the ingestion boundary is to feed NON-NFC input through
`ingest.normalize_tree` and assert the canon output is the NFC'd (composed / reordered) form, and
that it equals the canon output of the already-composed input.

This test owns the NFC-correctness assertions that canon v1's NFC vectors used to carry (now stored
as `*-verbatim` in canon-vectors.json for canon's own byte-identity coverage):
  - decomposed 'e'+U+0301 ingests to precomposed U+00E9 (string value AND object key)
  - a post-NFC key collision is detected at ingest (canon v2 itself no longer raises)
  - post-13 combining marks reorder by canonical combining class under the pinned U16 DB

All non-ASCII is built with chr() so the exact codepoints are unambiguous in source.

Run: python3 canon/conformance/test_ingest_nfc.py   (exit 0 = pass)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "..", "py")
sys.path.insert(0, PY)

import canon  # noqa: E402  (canon v2: verbatim, Unicode-DB-independent)
import canon_ingest as ingest  # noqa: E402  (the relocated ingest-boundary NFC normalizer + pin)

DECOMPOSED_EACUTE = "e" + chr(0x0301)   # 'e' + COMBINING ACUTE ACCENT -> NFC U+00E9
COMPOSED_EACUTE = chr(0x00E9)            # precomposed U+00E9

# U+0C15 TELUGU KA + U+0951 (ccc230) + U+0C3C TELUGU NUKTA (ccc7). NFC@16 reorders ccc7 (NUKTA)
# before ccc230 (U+0951); under a pre-15 DB the NUKTA reads ccc0 and the order is preserved.
POST13_RAW = chr(0x0C15) + chr(0x0951) + chr(0x0C3C)
POST13_NFC16 = chr(0x0C15) + chr(0x0C3C) + chr(0x0951)  # NUKTA reordered ahead of U+0951

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def test_value_nfc():
    """A non-NFC string VALUE ingests to its composed form before canon (which would emit it raw)."""
    ingested = ingest.normalize_string(DECOMPOSED_EACUTE)
    check(ingested == COMPOSED_EACUTE,
          "ingest did not compose 'e'+U+0301 to U+00E9: got %r" % ingested)
    # The whole point: canon over the INGESTED value equals canon over the already-composed value,
    # and canon over the RAW decomposed value differs (proving canon itself does NOT normalize).
    via_ingest = canon.canonical_bytes(ingest.normalize_tree(DECOMPOSED_EACUTE))
    composed = canon.canonical_bytes(COMPOSED_EACUTE)
    raw = canon.canonical_bytes(DECOMPOSED_EACUTE)
    check(via_ingest == composed,
          "ingest->canon(decomposed) != canon(composed); ingest NFC is not effective")
    check(raw != composed,
          "canon(decomposed)==canon(composed): canon v2 is still normalizing (must NOT)")


def test_key_nfc():
    """A non-NFC object KEY ingests to its composed form (canon v2 sorts already-NFC keys as-is)."""
    ingested = ingest.normalize_tree({DECOMPOSED_EACUTE: 1})
    check(list(ingested.keys()) == [COMPOSED_EACUTE],
          "ingest did not compose a decomposed object key: %r" % list(ingested.keys()))
    via_ingest = canon.canonical_bytes(ingest.normalize_tree({DECOMPOSED_EACUTE: 1}))
    composed = canon.canonical_bytes({COMPOSED_EACUTE: 1})
    check(via_ingest == composed,
          "ingest->canon(decomposed key) != canon(composed key); ingest key NFC is not effective")


def test_key_collision_detected_at_ingest():
    """Two keys that collide AFTER NFC must be rejected at ingest (canon v2 no longer detects this)."""
    raised = False
    try:
        ingest.normalize_keyed({DECOMPOSED_EACUTE: 1, COMPOSED_EACUTE: 2})
    except ingest.IngestError:
        raised = True
    check(raised, "ingest did not detect a post-NFC key collision (decomposed vs composed eacute)")
    # And confirm canon v2 itself does NOT raise on the same distinct-codepoint dict (the collision
    # detection genuinely moved to ingest).
    try:
        canon.canonical_bytes({DECOMPOSED_EACUTE: 1, COMPOSED_EACUTE: 2})
        canon_raised = False
    except canon.CanonError:
        canon_raised = True
    check(not canon_raised,
          "canon v2 still raises a key-collision error; collision detection did not relocate")


def test_post13_reorder():
    """Post-13 combining marks reorder by canonical combining class through ingest (pinned U16 DB)."""
    ingested = ingest.normalize_string(POST13_RAW)
    check(ingested == POST13_NFC16,
          "ingest did not reorder post-13 combining marks by ccc under the pinned U16 DB: %r"
          % ingested)
    # canon over the raw input preserves order (verbatim); ingest reorders. They MUST differ.
    check(canon.canonical_bytes(POST13_RAW) != canon.canonical_bytes(ingested),
          "canon(raw) == canon(ingested) for a post-13 reorder; ingest NFC is not effective")


def main():
    print("== canon v2 ingest-NFC enforcement test ==")
    test_value_nfc()
    test_key_nfc()
    test_key_collision_detected_at_ingest()
    test_post13_reorder()
    if failures:
        print("INGEST-NFC ENFORCEMENT: FAIL (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("INGEST-NFC ENFORCEMENT: PASS (value + key NFC, collision detection, post-13 reorder)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
