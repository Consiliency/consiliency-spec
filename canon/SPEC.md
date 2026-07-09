# canon v2 — Canonical Serialization + Content-Addressing Contract

Status: **NORMATIVE**, frozen for v2. Owned by `spec` (Consiliency). This is the foundational
content-addressing contract (**S1**) on which the rest of the engine builds.

> **canon v1 → v2 (NFCBOUNDARY).** v2 is identical to v1 except that mandatory Unicode **NFC is no
> longer applied inside `canonical_bytes`** — it relocated to the **ingestion boundary** (callers
> deliver already-NFC content; see §5 and `py/canon_ingest.py`). canon v2 is therefore **independent
> of any Unicode DB version**: the `unicodedata2` pin and the fail-closed version assertion moved to
> the ingest boundary. The digest **domain prefix changed `spec-canon:v1:` → `spec-canon:v2:`** (§8),
> so a v2 digest can never collide with a v1 digest of the same input. Every other rule — key
> code-point sort, integers-only, lone-surrogate rejection, minimal escaping, the four profiles, the
> content/envelope split — is **unchanged**. Historical v1 certificates remain valid *as v1 records*;
> see §9. Driver: escape the Unicode-16.0 *in-hash* NFC pin so canon's byte-identity gate is no longer
> Unicode-version-coupled.

This document is the single source of truth. A conformance vector suite
(`vectors/canon-vectors.json`) ships with it and is the executable form of every rule below.
Two reference ports (Python `py/canon.py`, TypeScript `ts/canon.ts`) MUST agree **byte-for-byte**
on every vector. That cross-language byte-identity is the exit gate.

---

## 0. Scope and the one hard guarantee

`canon` defines **one** function over a restricted value domain:

```
canonical_bytes(value) -> bytes        # deterministic, language-independent UTF-8
digest(value, profile) -> hex string   # SHA-256 over a domain-separated wrapping of those bytes
```

The hard guarantee: **for any value in the supported domain, every conforming implementation in
every language produces the identical byte string and the identical digest.** Nothing else in the
system may content-address by re-serializing; see §9 (produce once, store verbatim).

---

## 1. Supported value domain

Canonical content is built only from these JSON-like types:

| Type        | Notes |
|-------------|-------|
| object/map  | string keys only; sorted by key (see §3) |
| array/list  | insertion order preserved ALWAYS (see §4) |
| string      | already-NFC (normalized at ingest, not by canon), UTF-8 (see §5) |
| integer     | arbitrary precision; NO floats (see §6) |
| boolean     | `true` / `false` lowercase |
| null        | `null` lowercase |

**Explicitly rejected** (encoder MUST raise a clear error, never coerce):
floats / real numbers, `NaN`, `+Infinity`, `-Infinity`, non-string object keys, **unpaired
surrogate code points U+D800–U+DFFF in any string** (see §5), and any language-native type not in
the table (dates, sets, bytes, undefined, functions, etc.).
Decimals are the caller's responsibility — pre-represent them as strings or scaled integers
**before** handing a value to `canon`. This deliberately sidesteps cross-language float formatting,
the single largest source of divergence, for v1.

---

## 2. The vector-input encoding (type-tagged) — why JSON can't carry the inputs directly

The conformance vectors live in a JSON file, but **raw JSON cannot losslessly carry the inputs we
must test**, and the failure is silent and language-asymmetric:

- `NaN` / `Infinity` are not valid JSON: `JSON.parse` throws, Python `json.loads` accepts them.
- `1.0` parses to a float in Python (rejectable) but to the integer `1` in JS (`Number.isInteger`
  is true) — so an "int vs float" vector would diverge purely from *parsing*, before the encoder runs.
- Integers above 2^53 lose precision under `JSON.parse`.

Therefore the `input` field of every vector is a **type-tagged tree** decoded by a tiny shared
decoder that is identical in both languages. Tags:

| Tag form                | Decodes to |
|-------------------------|-----------|
| `{"$int": "123"}`       | integer (decimal string, arbitrary precision, optional leading `-`) |
| `{"$float": "1.0"}`     | a float marker — the encoder MUST reject it |
| `{"$nan": true}`        | NaN marker — encoder MUST reject |
| `{"$inf": 1}` / `{"$inf": -1}` | +/-Infinity marker — encoder MUST reject |
| `{"$str": "..."}`       | string (explicit; also used to carry a key that looks like a tag) |
| `{"$bool": true}`       | boolean (explicit) |
| `{"$null": true}`       | null (explicit) |
| `{"$obj": {k: <tagged>, ...}}` | object; keys are literal strings, values tagged |
| `{"$arr": [<tagged>, ...]}` | array; elements tagged |

A bare JSON `true`/`false`/`null`/string/array/object is also accepted by the decoder as the
obvious thing, but the canonical vectors use explicit tags wherever type intent is load-bearing
(numbers, booleans, the NaN/Inf/float rejections) so the file is unambiguous in every language.

The decoder is part of the **test harness**, not the canon contract; `canonical_bytes` operates on
already-decoded native values. But both ports MUST decode the file identically, so the decoder is
specified here and implemented identically in `py/canon.py` and `ts/canon.ts`.

---

## 3. Object keys — sorted by Unicode code point, at every depth

Keys are sorted ascending by **Unicode code point**, recursively at every nesting level.

**Trap (load-bearing):** JavaScript's default string comparison (`Array.prototype.sort` /
`<`) compares **UTF-16 code units**, which is WRONG for astral-plane characters (U+10000 and
above). A high-BMP character such as U+E000 must sort *before* an astral character U+10000, but
under code-unit comparison the astral char's leading surrogate (0xD800) compares as less than
0xE000, flipping the order. Implementations MUST compare by code point (iterate code points /
`codePointAt`), never by raw UTF-16 unit. Python's default `str` comparison is already by code
point. The `astral-key-sort` vector exists specifically to fail any code-unit implementation.

Keys MUST be **already NFC** when they reach canon (canon v2 does NOT normalize — see §5). The
ingestion boundary (`py/canon_ingest.py`) NFC-normalizes keys **before** canon sees them, and that
is where the **key-collision check** now lives: if two distinct input keys normalize to the same NFC
string, the ingest boundary MUST raise an error (key collision), never silently overwrite. canon v2
sorts the already-NFC keys by code point as-is.

---

## 4. Arrays — insertion order preserved ALWAYS

Array element order is **never** changed by the serializer. Reordering an array yields different
canonical bytes and a different digest; this is intentional and tested (`array-order-matters`).
The only lists that may be ordered differently are those a *schema* explicitly declares
order-insensitive (e.g. candidate-id sets) — and that ordering is performed by the schema/caller
*before* canon sees the value. The serializer itself sorts nothing in arrays.

---

## 5. Strings — already-NFC, raw UTF-8, minimal escaping

0. **Reject unpaired surrogates** (any code point U+D800–U+DFFF not part of a valid pair). This is
   load-bearing for byte-identity: a lone surrogate makes Python's UTF-8 encoder *raise* while
   JavaScript's `TextEncoder` silently emits U+FFFD (`EF BF BD`) — a silent cross-language byte
   divergence. canon forbids it in both (vector `reject-lone-surrogate`). Surrogate rejection is a
   validity rule and STAYS in canon v2 (it is not NFC).
1. **canon v2 does NOT normalize NFC.** Strings reaching canon MUST already be NFC. NFC is applied
   once at the **ingestion boundary** (`py/canon_ingest.py.normalize_string` / `normalize_tree`),
   before any value reaches canon — see the boundary note below. (canon v1 normalized here; v2
   relocated it to escape the in-hash Unicode-version pin.)
2. Emit as UTF-8.
3. **Escaping — escape only what is required:**
   - `"` -> `\"`
   - `\` -> `\\`
   - every control character U+0000–U+001F -> `\uXXXX` with **lowercase** hex (e.g. newline U+000A
     is emitted as the six-character sequence backslash-u-0-0-0-a, NOT `\n`). There are **no**
     short escapes (`\n`, `\t`, `\b`, `\f`, `\r` are NOT produced).
   - every other character, including all non-ASCII (é, 日本語, emoji, astral), is emitted as
     **raw UTF-8** — never `\uXXXX`. This is the `ensure_ascii=False` equivalent. U+007F (DEL)
     and other non-control characters are emitted raw; only U+0000–U+001F are escaped.

String output is `"` + escaped-contents + `"`.

> **Unicode-database pin (NFC determinism) — RELOCATED to the ingestion boundary (canon v2).** NFC
> is defined against a Unicode version, so wherever it is applied the implementations must use the
> SAME Unicode DB or NFC can diverge for codepoints assigned after the older DB. In canon **v1** NFC
> ran *inside* the hash, so `canonical_bytes` depended on the pinned Unicode DB. **canon v2 performs
> no NFC and depends on no Unicode DB version** — the pin moved to the ingest boundary
> (`py/canon_ingest.py`), which **pins Python to the `unicodedata2==16.0.0` backport** (NOT stdlib
> `unicodedata`, which is bound to the host CPython build — 13.0.0 on CPython 3.10) and **asserts at
> import time, fail-closed**, that its Unicode version reduces to `16.0`. All production ingest is
> Python; the TypeScript canon port is the conformance reference and needs no ingest normalizer.
> `conformance/check.sh` installs the pin and runs (a) canon self-conformance + cross-language
> byte-identity, and (b) the ingest-boundary tests — `test_ingest_nfc.py` (feed NON-NFC input through
> ingest → canon and assert the normalized form) and `test_unicode_skew.py` (the fail-closed assertion
> must fire on a simulated stale DB; the `post13-*` inputs must byte-diverge between a Unicode-13 DB
> and the pinned Unicode-16 DB). The ingest tests are load-bearing precisely because the byte-identity
> gate **cannot** catch a missing/wrong pin on its own: canon v2 passes bytes through verbatim, so
> both ports stay byte-identical even if ingest NFC were absent. The `post13-*` inputs exercise exactly
> the codepoints (U+0C3C ccc=7, U+0897 ccc=230, U+1715 ccc=9) that would diverge under the old skew.

---

## 6. Numbers — integers only

- **Integers only.** Emitted as the shortest decimal representation: optional leading `-`, then
  digits with no leading zeros (except `0` itself), no `+`, no exponent, no decimal point, no
  trailing `.0`. So both `1` and a hypothetical `1.0` input would have to be the *integer* `1`;
  there is no `1.0` form because floats are rejected.
- **Floats, `NaN`, `+Infinity`, `-Infinity` are REJECTED** with a clear error (`allow_nan=false`
  and more — no real numbers at all). The caller pre-represents decimals as strings or scaled
  integers.
- Arbitrary precision: integers beyond 2^53 are supported. Python uses native `int`. TypeScript
  uses `bigint` for emission so large integers do not lose precision; the `$int` decoder produces
  a `bigint`, and the encoder accepts both `bigint` and a safe-range `number` that is an integer.
  A non-integer `number` in TS (e.g. `1.5`) is treated as a float and REJECTED.

`true` / `false` / `null` are lowercase literals.

> **Python boolean trap (load-bearing):** `isinstance(True, int)` is `True` in Python, so the
> type dispatch MUST test `bool` **before** `int`, or `true` would serialize as `1`. JS has no
> such overlap. The `boolean-value` vector guards this asymmetry.

---

## 7. Structural separators

- Object: `{` + entries joined by `,` + `}`, each entry is `key-string` + `:` + value.
- Array: `[` + elements joined by `,` + `]`.
- **No insignificant whitespace anywhere** — no spaces after `:` or `,`, no newlines, no indent.
- Empty object is `{}`, empty array is `[]`.

---

## 8. Digest — domain-separated SHA-256

```
digest(value, profile) = lowercase_hex( SHA-256( domain_prefix(profile) || canonical_bytes(value) ) )
domain_prefix(profile) = ASCII("spec-canon:v2:" + profile + "\n")
```

- The `\n` is a single ASCII line feed (0x0a). The prefix is pure ASCII bytes, prepended to the
  canonical UTF-8 bytes **before** hashing. Domain separation prevents a digest under one profile
  from ever colliding with another profile's digest of the same content.
- **The `v2` literal in the prefix is the canon-version domain separator.** Because the prefix is
  part of the digest preimage, a `spec-canon:v2:` digest can **never** collide with a `spec-canon:v1:`
  digest of the same input — even for already-NFC content where canon v1 and v2 emit identical
  `canonical_bytes`. This is what makes the v1→v2 boundary observable and the historical-cert policy
  (§9) well-defined.
- **Digest profiles (the verbatim v1 contract set):**
  - `semantic-content` — the meaning-bearing content of an artifact.
  - `run` — a run record (kept separate from semantic content).
  - `artifact-byte` — the byte identity of a stored artifact.
  - `certificate` — a parity certificate.
- Hex is **lowercase**.
- **Digest-field exclusion:** if the top-level value is an object containing a key named `digest`,
  that key/value pair is removed before canonicalization so a record can carry its own digest
  without the digest depending on itself. Exclusion is **top-level only** (a nested object's
  `digest` key is ordinary content). This is documented and tested (`digest-field-excluded`).
  - **Preimage caveat (adopters read this):** exclusion happens **only in `digest()`**, not in
    `canonical_bytes()`. So for a digest-bearing record, `canonical_bytes(record)` is **NOT** the
    digest preimage — the preimage is `canonical_bytes(record_without_top_level_digest)`. The
    `digest-field-excluded` vector reflects this: its stored `expected_canonical_bytes_b64`
    *includes* the `digest` key, while its `expected_digest_hex` is computed over the stripped
    record (and equals `digest-field-excluded-baseline`). Do not expect
    `sha256(prefix || stored_bytes) == stored_digest` for such a record.

### Non-hashed `locator` envelope

`locator` is **not** a digest profile — it is the documented home for volatile / provenance data
that MUST NEVER enter canonical content: wall-clock timestamps, absolute filesystem paths,
locale-dependent collation, hostnames, PIDs, ephemeral run ids. See §10.

---

## 9. Produce once, store verbatim, never recompute

A digest and its canonical bytes are produced **once**, at authorship, and stored verbatim.
Consumers compare the **stored** bytes/digest; **any consumer that re-serializes a value to
compare it is a bug.** The public API is intentionally just `canonical_bytes(value)` and
`digest(value, profile)` — there is no "re-canonicalize and diff" helper, by design.

### Historical v1 certificates (canon v1→v2 policy — NORMATIVE)

The canon v1→v2 boundary is a **certificate-version boundary**. The policy (chosen for NFCBOUNDARY):

- **A v1 certificate stays valid *as a v1 record*.** It is identified by `canon_version = "v1"` and a
  `spec-canon:v1:` digest domain. v2 is the new authoring default (`canon_version = "v2"`,
  `spec-canon:v2:`); v1 artifacts are read-only history.
- **A v1 digest is NEVER compared against a v2 digest.** Per "produce once, store verbatim, never
  recompute" above, a stored v1 digest is never re-serialized under v2 (and the `v2` prefix makes the
  two domains provably disjoint, §8). Consumers branch on `canon_version`.
- **A mixed-`canon_version` graph is REJECTED at the consumer** (single-`canon_version` invariant) —
  never silently re-hashed to reconcile the two.

Downstream certificate consumers cut over to v2 **wholesale**;
the v1→v2 boundary is a metadata-only notification to them, not an in-place re-hash.

---

## 10. Splitting content from envelope (volatile data)

Volatile/provenance data must live outside hashed content. The reference impls provide a
documented helper:

```
split_record(record, content_keys) -> (content, envelope)
```

`content` holds only the keys in `content_keys` (hashed via `canonical_bytes` / `digest`);
`envelope` holds everything else (the non-hashed locator: wall-clock, abs paths, locale, host,
pid, etc.). Changing envelope fields MUST NOT change the digest — proven by the
`content-envelope-split` vector, where the same content with two different envelopes yields the
identical `semantic-content` digest.

---

## 11. Conformance and the exit gate

`vectors/canon-vectors.json` is an array of:

```json
{
  "name": "...",
  "input": <type-tagged tree, or {"$error": "..."} for reject cases>,
  "profile": "semantic-content | run | artifact-byte | certificate",
  "expected_canonical_bytes_b64": "<base64 of the canonical UTF-8 bytes>",
  "expected_digest_hex": "<lowercase sha-256 hex>"
}
```

Reject vectors (float/NaN/Inf) carry `"expect_error": true` instead of expected bytes/digest;
both ports MUST raise.

The expected values are **generated by the Python reference impl** (never hand-authored base64/hex)
and pinned in the file. The TS impl must reproduce them.

**EXIT GATE** — `bash conformance/check.sh` (canon v2):
0. installs the ingest-boundary Unicode pin (`unicodedata2`), then
1. runs `py/test_canon.py` (Python canon vs the pinned vectors), and
2. runs `ts/canon.test.ts` (TS canon vs the **same** pinned vectors), and
3. runs the **ingest-boundary** tests — `conformance/test_ingest_nfc.py` (NON-NFC input through
   ingest → canon yields the normalized form) and `conformance/test_unicode_skew.py` (the fail-closed
   pin assertion fires on a stale DB; the `post13-*` inputs are live U13-vs-U16 discriminators), and
4. asserts Python and TS canon produced byte-identical canonical output and identical digests on
   **every** vector.

Step 3 is load-bearing and not redundant: canon v2 emits bytes verbatim, so the cross-language
byte-identity check (step 4) would stay green even if ingest NFC were missing entirely — only the
ingest tests prove NFC enforcement actually lives at the boundary and that its pin is load-bearing.

If any vector cannot be made byte-identical across languages, that is a contract defect to be
reported and fixed — never papered over.

## 12. canon-core API freeze (IF-0-XG4-1)

`canon/core` is the single portable implementation of the canon v2 algorithm. The frozen core
surface is:

- `canonical_bytes(value) -> bytes`
- `digest(value, profile) -> lowercase sha256 hex`
- `canonical_bytes_from_json(tagged_json) -> bytes`
- `digest_from_json(tagged_json, profile) -> lowercase sha256 hex`

The JSON entrypoints consume the same type-tagged tree used by `vectors/canon-vectors.json`.
The Rust core keeps NFC out of the hash exactly like the Python and TypeScript references: callers
deliver already-normalized content from the ingest boundary, and `digest` applies
`spec-canon:v2:<profile>\n || canonical_bytes(strip_top_level_digest(value))`.

The binding surfaces are thin wrappers over that core:

- WASM exports `canonicalBytesFromJson` and `digestFromJson`.
- PyO3 exports `canonical_bytes_from_json` and `digest_from_json` from module `canon_core`.

`conformance/check_xg4_canon_core.sh` is the XG4 exit gate. It runs the existing Python/TypeScript
canon gate, runs Rust vector tests, diffs Python/TypeScript/Rust emitted bytes and digests over the
full corpus, and compiles both binding surfaces. Consumers must dual-run against this corpus before
removing a vendored implementation.

## Open items (0A follow-ups, tracked)
- **In-hash Unicode-version coupling. — RESOLVED by canon v2 / NFCBOUNDARY (2026-06).** Even after the
  0A pin below, canon v1's `canonical_bytes` *itself* still depended on the pinned Unicode DB (NFC ran
  in-hash), so the byte-identity gate stayed Unicode-version-coupled. **canon v2 relocates NFC to the
  ingestion boundary** (`py/canon_ingest.py`): canon no longer normalizes and no longer depends on any
  Unicode DB; the `unicodedata2==16.0.0` pin + fail-closed assertion + the post-13 discriminators moved
  to the ingest tests, and the digest prefix bumped `v1`→`v2` (§8). The pin **relocated, it did not
  vanish** — a deterministic Unicode DB is still required at ingest for cross-language NFC. See §5, §8,
  §9 (historical-cert policy).
- **`idmodel` boundary-id NFC (follow-on, NOT addressed here).** Removing canon's in-hash NFC *unmasks*
  `idmodel`'s own NFC: `idmodel/py/idmodel.py` normalizes occurrence-id content with **stdlib**
  `unicodedata` (host-CPython Unicode version), and under v1 canon (unicodedata2 16.0) re-normalized that
  content so canon was authoritative. Under v2 canon no longer re-NFCs, so idmodel's stdlib NFC becomes
  authoritative for occurrence-ids — a possibly-unpinned Unicode dependency. All committed idmodel content
  is NFC-invariant (verified), so no current artifact is affected; harmonizing idmodel onto the same
  pinned ingest normalizer is a named follow-on, out of NFCBOUNDARY's "NFC placement only" scope.
- **Unicode DB version skew (0A). — RESOLVED (2026-06).** Previously: Python stdlib `unicodedata` (13.0.0 on
  CPython 3.10) vs Node Unicode 16.0, leaving a determinism hole for identifiers using codepoints assigned
  *after* Unicode 13 (their NFC could differ between ports). Resolution (canon v1; the pin now lives at the
  v2 ingest boundary):
  - **Pinned Unicode version: 16.0** (matches Node's `process.versions.unicode`).
  - **Python NFC now uses the `unicodedata2==16.0.0` backport** (exact pin in `py/requirements.txt`), NOT
    stdlib `unicodedata`. `unicodedata2.unidata_version` is `16.0.0` → reduces to `16.0`, byte-identical NFC
    to Node ICU 16.0 (confirmed on every vector by the cross-language gate).
  - **Fail-closed version assertion at import time** in BOTH ports (`canon.py` and `canon.ts`): the Unicode
    version they use must reduce to `16.0` or they refuse to load — a mismatch can never silently diverge.
  - **Post-13 conformance vectors** added (`post13-nfc-reorder-telugu-nukta`, `…-arabic-pepet`,
    `…-two-new-marks`) using combining marks assigned in Unicode 15.0/16.0 (U+0C3C ccc=7, U+0897 ccc=230,
    U+1715 ccc=9). Their NFC canonical-reordering differs under a Unicode-13 DB (those marks read as ccc=0)
    vs the pinned Unicode-16 DB — verified byte-divergent by `conformance/test_unicode_skew.py`. They pass
    now because both ports use 16.0.
  - **Gate enforcement:** `conformance/check.sh` installs the pin, hard-asserts both ports report `16.0`, runs
    the negative tests (simulated stale DB must trip the assertion; post-13 vectors must byte-diverge under a
    U13 DB), then runs the cross-language byte-identity check over all vectors.
