"""Generate canon-vectors.json from the Python reference implementation (SPEC.md section 11).

Expected canonical bytes (base64) and digest (hex) are GENERATED here, never hand-authored, then
pinned. The TypeScript port must reproduce them byte-for-byte. Run:

    python3 canon/vectors/gen_vectors.py
"""

import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py"))

import canon  # noqa: E402

# Each spec: name, input (type-tagged tree), profile, and optionally expect_error.
# Inputs use the §2 tag encoding so type intent survives JSON parsing identically in both langs.
SPECS = [
    # --- object key ordering: reordered keys -> identical bytes ---
    {
        "name": "object-keys-sorted",
        "input": {"$obj": {"b": {"$int": "2"}, "a": {"$int": "1"}, "c": {"$int": "3"}}},
        "profile": "semantic-content",
    },
    {
        "name": "object-keys-sorted-reordered",  # same content, different author order
        "input": {"$obj": {"c": {"$int": "3"}, "a": {"$int": "1"}, "b": {"$int": "2"}}},
        "profile": "semantic-content",
    },
    # --- arrays preserve order; reorder -> DIFFERENT bytes ---
    {
        "name": "array-order-preserved",
        "input": {"$arr": [{"$int": "1"}, {"$int": "2"}, {"$int": "3"}]},
        "profile": "semantic-content",
    },
    {
        "name": "array-order-matters",  # reversed -> must differ from array-order-preserved
        "input": {"$arr": [{"$int": "3"}, {"$int": "2"}, {"$int": "1"}]},
        "profile": "semantic-content",
    },
    # --- canon v2: non-ASCII / combining-mark bytes pass through VERBATIM (NFC moved to ingest) ----
    # canon v2 no longer normalizes NFC (it relocated to canon/py/canon_ingest.py). These inputs now exercise
    # canon's VERBATIM byte emission + cross-language byte-identity for precomposed, decomposed, and
    # post-Unicode-13 combining-mark sequences — coverage that must stay on the canon gate. The
    # NFC-normalization-correctness assertions for these SAME inputs live in the Python ingest
    # conformance test (canon/conformance/test_ingest_nfc.py): it feeds them through ingest -> canon and
    # asserts the NFC'd form, incl. the U13-vs-U16 post-13 reorder discriminator.
    {
        "name": "composed-eacute-verbatim",
        "input": {"$str": "é"},  # U+00E9 (precomposed é)
        "profile": "semantic-content",
    },
    {
        "name": "decomposed-eacute-verbatim",
        "input": {"$str": "é"},  # 'e' + U+0301 combining acute -> NFC to U+00E9
        "profile": "semantic-content",
    },
    # canon v2 emits the decomposed key verbatim (no key NFC). The ingest test asserts the key
    # normalizes + collapses to its composed form (and detects a post-NFC key collision).
    {
        "name": "decomposed-key-verbatim",
        "input": {"$obj": {"é": {"$int": "1"}}},
        "profile": "semantic-content",
    },
    # --- post-Unicode-13 combining-mark sequences: canon v2 emits VERBATIM (no ccc reorder) --------
    # Under canon v1 these proved canon NFC-reordered combining marks under the pinned U16 DB. canon v2
    # does NOT reorder — it emits the input sequence verbatim — so on the canon gate they now assert
    # verbatim byte-identity across the two ports. The U13-vs-U16 reorder DISCRIMINATOR (the load-bearing
    # pin proof) moved to canon/conformance/test_ingest_nfc.py, which runs ingest.normalize_tree on the
    # SAME inputs and asserts the NFC@16 reorder. Built with chr() so no exotic bytes live in source.
    {
        # U+0C15 TELUGU KA + U+0951 (ccc230) + U+0C3C TELUGU NUKTA (ccc7, U15.0).
        "name": "post13-telugu-nukta-verbatim",
        "input": {"$str": chr(0x0C15) + chr(0x0951) + chr(0x0C3C)},
        "profile": "semantic-content",
    },
    {
        # U+0628 + U+0897 ARABIC PEPET (ccc230, U16.0) + U+065C (ccc220).
        "name": "post13-arabic-pepet-verbatim",
        "input": {"$str": chr(0x0628) + chr(0x0897) + chr(0x065C)},
        "profile": "semantic-content",
    },
    {
        # U+1715 TAGALOG PAMUDPOD (ccc9, U15.0) + U+0C3C TELUGU NUKTA (ccc7, U15.0).
        "name": "post13-two-new-marks-verbatim",
        "input": {"$str": chr(0x1715) + chr(0x0C3C)},
        "profile": "semantic-content",
    },
    # --- astral-plane key sorting: U+E000 (BMP) must sort BEFORE U+10000 (astral) ---
    # Under V8's default UTF-16 code-unit sort the astral key (high surrogate 0xD800 < 0xE000)
    # wrongly sorts first; correct code-point order puts U+E000 first.
    {
        "name": "astral-key-sort",
        "input": {"$obj": {
            "\U00010000": {"$int": "2"},  # astral, code point 0x10000
            "": {"$int": "1"},      # BMP private-use, code point 0xE000
        }},
        "profile": "semantic-content",
    },
    # --- non-ASCII emitted raw (no \uXXXX) ---
    {
        "name": "non-ascii-raw",
        "input": {"$str": "日本語 \U0001f600"},  # 日本語 + emoji
        "profile": "semantic-content",
    },
    # --- control chars escaped as \uXXXX lowercase; U+007F raw ---
    {
        "name": "control-chars-escaped",
        # LF(U+000A), TAB(U+0009), NUL(U+0000), US(U+001F) built via chr() so no raw
        # control bytes live in this source file.
        "input": {"$str": "a" + chr(0x0A) + "b" + chr(0x09) + "c" + chr(0x00) + "d" + chr(0x1F)},
        "profile": "semantic-content",
    },
    {
        "name": "del-char-raw",  # U+007F DEL is NOT a control (<=001F) -> emitted raw
        "input": {"$str": "x" + chr(0x7F) + "y"},
        "profile": "semantic-content",
    },
    # --- quote and backslash escaping ---
    {
        "name": "quote-backslash-escape",
        "input": {"$str": "a\"b\\c"},
        "profile": "semantic-content",
    },
    # --- booleans (Python bool-before-int trap) and null ---
    {
        "name": "boolean-value",
        "input": {"$obj": {"t": {"$bool": True}, "f": {"$bool": False}}},
        "profile": "semantic-content",
    },
    {
        "name": "null-value",
        "input": {"$null": True},
        "profile": "semantic-content",
    },
    # --- integers incl. large / negative / zero ---
    {
        "name": "integer-basic",
        "input": {"$int": "42"},
        "profile": "semantic-content",
    },
    {
        "name": "integer-negative-zero-large",
        "input": {"$arr": [{"$int": "0"}, {"$int": "-17"},
                            {"$int": "123456789012345678901234567890"}]},
        "profile": "semantic-content",
    },
    # --- nested structures ---
    {
        "name": "nested-object-array",
        "input": {"$obj": {
            "z": {"$arr": [{"$obj": {"k": {"$str": "v"}}}, {"$int": "1"}]},
            "a": {"$obj": {"nested": {"$arr": [{"$bool": True}, {"$null": True}]}}},
        }},
        "profile": "semantic-content",
    },
    # --- empty object / array ---
    {
        "name": "empty-object",
        "input": {"$obj": {}},
        "profile": "semantic-content",
    },
    {
        "name": "empty-array",
        "input": {"$arr": []},
        "profile": "semantic-content",
    },
    # --- each digest profile (same content, different domain prefix -> different digest) ---
    {
        "name": "profile-semantic-content",
        "input": {"$obj": {"id": {"$str": "x"}}},
        "profile": "semantic-content",
    },
    {
        "name": "profile-run",
        "input": {"$obj": {"id": {"$str": "x"}}},
        "profile": "run",
    },
    {
        "name": "profile-artifact-byte",
        "input": {"$obj": {"id": {"$str": "x"}}},
        "profile": "artifact-byte",
    },
    {
        "name": "profile-certificate",
        "input": {"$obj": {"id": {"$str": "x"}}},
        "profile": "certificate",
    },
    # --- top-level digest field excluded from its own digest ---
    {
        "name": "digest-field-excluded",
        "input": {"$obj": {
            "digest": {"$str": "PLACEHOLDER-MUST-NOT-AFFECT-DIGEST"},
            "content": {"$str": "real"},
        }},
        "profile": "semantic-content",
    },
    {
        "name": "digest-field-excluded-baseline",  # same minus the digest key -> same digest
        "input": {"$obj": {"content": {"$str": "real"}}},
        "profile": "semantic-content",
    },
    # --- content/envelope split: two envelopes, identical content digest ---
    # We store the CONTENT subset that split_record would keep; the vector proves the digest is
    # over content only. (envelope_a / envelope_b are illustrative, not encoded.)
    {
        "name": "content-envelope-split",
        "input": {"$obj": {"capability": {"$str": "validate"}, "version": {"$int": "1"}}},
        "profile": "semantic-content",
        "_note": "digest must equal content-envelope-split-other; envelopes differ, content same",
    },
    {
        "name": "content-envelope-split-other",
        "input": {"$obj": {"capability": {"$str": "validate"}, "version": {"$int": "1"}}},
        "profile": "semantic-content",
        "_note": "same content as content-envelope-split with a different (non-hashed) envelope",
    },
    # --- reject cases (float / NaN / Infinity) ---
    {
        "name": "reject-float",
        "input": {"$float": "1.0"},
        "profile": "semantic-content",
        "expect_error": True,
    },
    {
        "name": "reject-nan",
        "input": {"$nan": True},
        "profile": "semantic-content",
        "expect_error": True,
    },
    {
        "name": "reject-pos-inf",
        "input": {"$inf": 1},
        "profile": "semantic-content",
        "expect_error": True,
    },
    {
        "name": "reject-neg-inf",
        "input": {"$inf": -1},
        "profile": "semantic-content",
        "expect_error": True,
    },
    {
        "name": "reject-float-nested",
        "input": {"$obj": {"price": {"$float": "9.99"}}},
        "profile": "semantic-content",
        "expect_error": True,
    },
    # Unpaired surrogate: Python raises on UTF-8 encode, JS emits U+FFFD -> silent divergence.
    # canon rejects in BOTH (SPEC.md section 5). The string carries a lone high surrogate U+D800.
    {
        "name": "reject-lone-surrogate",
        "input": {"$str": "\ud800"},
        "profile": "semantic-content",
        "expect_error": True,
    },
    # $int payload grammar (SPEC.md section 2): optional '-' then ASCII digits, nothing else. Each of
    # these was accepted by at least one port's native parser before the grammar was pinned
    # (Python int(): whitespace, '_', non-ASCII digits; JS BigInt(): whitespace, '0x', ''; Rust
    # parse::<BigInt>: '+'), so a payload could digest in one port and be rejected in another.
    # These reject at DECODE (the payload never becomes a value) — the harnesses count a decode-stage
    # CanonError as the expected rejection.
    {"name": "reject-int-payload-empty", "input": {"$int": ""},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-whitespace", "input": {"$int": " 42 "},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-plus", "input": {"$int": "+5"},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-underscore", "input": {"$int": "1_000"},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-hex", "input": {"$int": "0x1A"},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-nonascii-digits", "input": {"$int": "\u09e6\u09e7"},
     "profile": "semantic-content", "expect_error": True,
     "_note": "Bengali digits U+09E6 U+09E7: Python int() and \\d accept them; the grammar is ASCII-only"},
    {"name": "reject-int-payload-interior-nul", "input": {"$int": "12\u000034"},
     "profile": "semantic-content", "expect_error": True},
    {"name": "reject-int-payload-bare-minus", "input": {"$int": "-"},
     "profile": "semantic-content", "expect_error": True},
    # Grammar-valid payloads that normalise: the digits are the identity, not the spelling.
    {"name": "int-payload-leading-zeros", "input": {"$int": "007"},
     "profile": "semantic-content", "_note": "leading zeros are accepted and normalise to 7"},
    {"name": "int-payload-negative-zero", "input": {"$int": "-0"},
     "profile": "semantic-content", "_note": "-0 is accepted and normalises to 0"},
    {"name": "int-payload-big-negative", "input": {"$int": "-123456789012345678901234567890"},
     "profile": "semantic-content"},
]


def build():
    out = []
    for spec in SPECS:
        entry = {"name": spec["name"], "input": spec["input"], "profile": spec["profile"]}
        if "_note" in spec:
            entry["note"] = spec["_note"]
        if spec.get("expect_error"):
            # decode_input sits INSIDE the guard: a payload-grammar rejection happens at decode.
            try:
                canon.canonical_bytes(canon.decode_input(spec["input"]))
            except canon.CanonError:
                entry["expect_error"] = True
            else:
                raise SystemExit("vector %r expected an error but encoded cleanly" % spec["name"])
        else:
            value = canon.decode_input(spec["input"])
            cbytes = canon.canonical_bytes(value)
            entry["expected_canonical_bytes_b64"] = base64.b64encode(cbytes).decode("ascii")
            entry["expected_digest_hex"] = canon.digest(value, spec["profile"])
        out.append(entry)
    return out


def render(vectors):
    # ensure_ascii=True so the file is pure ASCII: non-ASCII inputs (and the lone-surrogate
    # reject vector, which cannot be written as raw UTF-8) are stored as \uXXXX escapes that
    # BOTH json.load (Python) and JSON.parse (JS) read back to the identical string. This is
    # the file transport only; it does not affect canon output (which is ensure_ascii=False).
    return json.dumps(vectors, ensure_ascii=True, indent=2) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = os.path.join(HERE, "canon-vectors.json")
    text = render(build())
    if argv == ["--check"]:
        # Gate mode: the committed corpus MUST be this generator's output (a hand-edited vector or a
        # generator change without regeneration is a silent corpus/reference drift).
        with open(path, "r", encoding="utf-8") as f:
            committed = f.read()
        if committed != text:
            sys.stderr.write("FAIL: %s is not the output of gen_vectors.py — regenerate it "
                             "(python3 canon/vectors/gen_vectors.py) and commit.\n" % path)
            return 1
        print("canon-vectors.json is the generator's output (%d vectors)" % text.count('"name":'))
        return 0
    if argv:
        sys.stderr.write("usage: gen_vectors.py [--check]\n")
        return 2
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %d vectors to %s" % (text.count('"name":'), path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
