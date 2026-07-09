/**
 * canon v2 — TypeScript reference implementation.
 *
 * Normative contract: ../SPEC.md. This is a clean-room custom canonical encoder; it does NOT use
 * JSON.stringify for canonical output (its escaping / number / key-order behavior is not
 * guaranteed identical across languages). Must agree byte-for-byte with py/canon.py on every
 * vector in ../vectors/canon-vectors.json.
 *
 * canon v2 (vs v1): Unicode NFC is NO LONGER applied inside canonicalBytes — callers deliver
 * already-NFC content (NFC happens once at the ingestion boundary; see canon/py/canon_ingest.py for the
 * Python ingest path — all production ingest is Python, this port is the conformance reference).
 * canon v2 is therefore Unicode-DB-independent and no longer asserts Node's Unicode version. The
 * digest domain prefix is "spec-canon:v2:", giving v1/v2 domain separation (SPEC.md section 8).
 *
 * Public API (SPEC.md section 9):
 *   canonicalBytes(value) -> Uint8Array
 *   digest(value, profile) -> string (lowercase hex)
 *
 * Helpers:
 *   splitRecord(record, contentKeys) -> { content, envelope }   (SPEC.md section 10)
 *   decodeInput(tagged) -> CanonValue                            (SPEC.md section 2; test harness)
 */

import { createHash } from "node:crypto";

// canon v2 performs NO Unicode NFC and depends on NO Unicode DB version (SPEC.md section 5). NFC is
// applied at the ingestion boundary, which owns the relocated Unicode pin and fail-closed assertion.
// canon v2 only requires that keys are already NFC so its code-point sort is stable.

// SPEC.md section 8 — the four digest profiles. "locator" is intentionally NOT a profile.
export const PROFILES = ["semantic-content", "run", "artifact-byte", "certificate"] as const;
export type Profile = (typeof PROFILES)[number];
const DOMAIN_PREFIX = "spec-canon:v2:";

export class CanonError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CanonError";
  }
}

// Reject markers produced by the type-tagged decoder (SPEC.md section 2/6).
export class FloatMarker {}
export class NanMarker {}
export class InfMarker {
  constructor(public readonly sign: number) {}
}

// Supported canonical value domain (SPEC.md section 1). Integers use bigint for arbitrary
// precision; a plain `number` is accepted ONLY if it is a safe integer (non-integers are floats
// and rejected).
export type CanonValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | CanonValue[]
  | { [k: string]: CanonValue }
  | FloatMarker
  | NanMarker
  | InfMarker;

// --------------------------------------------------------------------------- //
// String encoding (SPEC.md section 5)
// --------------------------------------------------------------------------- //

function encodeString(s: string): string {
  // canon v2 does NOT normalize: callers deliver already-NFC content (see the ingest boundary).
  let out = '"';
  // Iterate by code point (the for..of iterator yields code points, handling surrogate pairs).
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (cp >= 0xd800 && cp <= 0xdfff) {
      // Unpaired surrogate: JS emits U+FFFD on encode while Python raises -> silent
      // byte-divergence. Reject in BOTH (SPEC.md section 5).
      throw new CanonError(
        "unpaired surrogate U+" + cp.toString(16).toUpperCase().padStart(4, "0") +
          " is not allowed in canonical content",
      );
    }
    if (ch === '"') {
      out += '\\"';
    } else if (ch === "\\") {
      out += "\\\\";
    } else if (cp <= 0x1f) {
      // Control chars: \uXXXX lowercase hex. No short escapes.
      out += "\\u" + cp.toString(16).padStart(4, "0");
    } else {
      // Everything else, incl. all non-ASCII and U+007F, is raw (ensure_ascii=False equivalent).
      out += ch;
    }
  }
  out += '"';
  return out;
}

// --------------------------------------------------------------------------- //
// Number encoding (SPEC.md section 6) — integers only.
// --------------------------------------------------------------------------- //

function encodeNumber(value: number | bigint): string {
  if (typeof value === "bigint") {
    return value.toString(10);
  }
  // A JS number: accept only safe integers; anything fractional / non-finite is a float -> reject.
  if (!Number.isFinite(value)) {
    throw new CanonError("NaN/Infinity are forbidden in canonical content");
  }
  if (!Number.isInteger(value)) {
    throw new CanonError("floats are forbidden in canonical content; pre-represent as int or string");
  }
  if (!Number.isSafeInteger(value)) {
    // Outside +/-(2^53-1) a JS number can't be trusted as an exact integer; require bigint.
    throw new CanonError("integer outside safe range; use bigint to avoid precision loss");
  }
  return value.toString(10);
}

// --------------------------------------------------------------------------- //
// Object encoding (SPEC.md sections 3, 7) — keys sorted by CODE POINT.
// --------------------------------------------------------------------------- //

/**
 * Compare two strings by Unicode code point (NOT UTF-16 code unit). JS default string compare is
 * code-unit order, which is wrong for astral chars; this is the load-bearing fix.
 */
function compareCodePoints(a: string, b: string): number {
  const ai = a[Symbol.iterator]();
  const bi = b[Symbol.iterator]();
  for (;;) {
    const an = ai.next();
    const bn = bi.next();
    if (an.done && bn.done) return 0;
    if (an.done) return -1; // a is a prefix of b
    if (bn.done) return 1;
    const acp = an.value.codePointAt(0)!;
    const bcp = bn.value.codePointAt(0)!;
    if (acp !== bcp) return acp < bcp ? -1 : 1;
  }
}

function encodeObject(obj: { [k: string]: CanonValue }): string {
  // canon v2 does NOT NFC-normalize keys (the ingest boundary did that, and detected post-NFC
  // collisions). canon sorts the already-NFC keys by Unicode code point as-is.
  const keys = Object.keys(obj).sort(compareCodePoints);
  const parts: string[] = [];
  for (const k of keys) {
    parts.push(encodeString(k) + ":" + encodeValue(obj[k]));
  }
  return "{" + parts.join(",") + "}";
}

function encodeArray(arr: CanonValue[]): string {
  // Insertion order preserved ALWAYS (SPEC.md section 4). Never sort.
  return "[" + arr.map(encodeValue).join(",") + "]";
}

// --------------------------------------------------------------------------- //
// Value dispatch (SPEC.md section 6 — booleans are distinct from numbers in JS).
// --------------------------------------------------------------------------- //

function encodeValue(value: CanonValue): string {
  if (value instanceof FloatMarker) {
    throw new CanonError("floats are forbidden in canonical content; pre-represent as int or string");
  }
  if (value instanceof NanMarker) {
    throw new CanonError("NaN is forbidden in canonical content");
  }
  if (value instanceof InfMarker) {
    throw new CanonError("Infinity is forbidden in canonical content");
  }
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" || typeof value === "bigint") return encodeNumber(value);
  if (typeof value === "string") return encodeString(value);
  if (Array.isArray(value)) return encodeArray(value);
  if (typeof value === "object") return encodeObject(value as { [k: string]: CanonValue });
  throw new CanonError("unsupported type for canonical content: " + typeof value);
}

// --------------------------------------------------------------------------- //
// Public API (SPEC.md sections 8, 9)
// --------------------------------------------------------------------------- //

export function canonicalBytes(value: CanonValue): Uint8Array {
  return new TextEncoder().encode(encodeValue(value));
}

function stripTopLevelDigest(value: CanonValue): CanonValue {
  // SPEC.md section 8: exclude a top-level "digest" key only.
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    !(value instanceof FloatMarker) &&
    !(value instanceof NanMarker) &&
    !(value instanceof InfMarker) &&
    Object.prototype.hasOwnProperty.call(value, "digest")
  ) {
    const out: { [k: string]: CanonValue } = {};
    for (const k of Object.keys(value as { [k: string]: CanonValue })) {
      if (k !== "digest") out[k] = (value as { [k: string]: CanonValue })[k];
    }
    return out;
  }
  return value;
}

export function digest(value: CanonValue, profile: Profile): string {
  if (!(PROFILES as readonly string[]).includes(profile)) {
    throw new CanonError("unknown digest profile: " + profile);
  }
  const prefix = Buffer.from(DOMAIN_PREFIX + profile + "\n", "ascii");
  const body = Buffer.from(canonicalBytes(stripTopLevelDigest(value)));
  return createHash("sha256").update(Buffer.concat([prefix, body])).digest("hex");
}

// --------------------------------------------------------------------------- //
// Content/envelope split (SPEC.md section 10)
// --------------------------------------------------------------------------- //

export function splitRecord(
  record: { [k: string]: CanonValue },
  contentKeys: string[],
): { content: { [k: string]: CanonValue }; envelope: { [k: string]: CanonValue } } {
  const keyset = new Set(contentKeys);
  const content: { [k: string]: CanonValue } = {};
  const envelope: { [k: string]: CanonValue } = {};
  for (const k of Object.keys(record)) {
    if (keyset.has(k)) content[k] = record[k];
    else envelope[k] = record[k];
  }
  return { content, envelope };
}

// --------------------------------------------------------------------------- //
// Type-tagged input decoder (SPEC.md section 2) — identical semantics to Python decode_input.
// --------------------------------------------------------------------------- //

type TaggedNode =
  | null
  | boolean
  | number
  | string
  | TaggedNode[]
  | { [k: string]: TaggedNode };

export function decodeInput(node: TaggedNode): CanonValue {
  if (node !== null && typeof node === "object" && !Array.isArray(node)) {
    const keys = Object.keys(node);
    if (keys.length === 1) {
      const tag = keys[0];
      const payload = (node as { [k: string]: TaggedNode })[tag];
      switch (tag) {
        case "$int":
          return BigInt(payload as string); // decimal string -> exact bigint
        case "$float":
          return new FloatMarker();
        case "$nan":
          return new NanMarker();
        case "$inf":
          return new InfMarker((payload as number) >= 0 ? 1 : -1);
        case "$str":
          return payload as string;
        case "$bool":
          return Boolean(payload);
        case "$null":
          return null;
        case "$obj": {
          const out: { [k: string]: CanonValue } = {};
          const p = payload as { [k: string]: TaggedNode };
          for (const k of Object.keys(p)) out[k] = decodeInput(p[k]);
          return out;
        }
        case "$arr":
          return (payload as TaggedNode[]).map(decodeInput);
        default:
          break; // one-key object that is not a tag: fall through
      }
    }
    const out: { [k: string]: CanonValue } = {};
    const p = node as { [k: string]: TaggedNode };
    for (const k of Object.keys(p)) out[k] = decodeInput(p[k]);
    return out;
  }
  if (Array.isArray(node)) return node.map(decodeInput);
  if (typeof node === "boolean") return node;
  if (typeof node === "number") {
    // A bare JSON number in a vector input: integral -> bigint; fractional -> float marker.
    return Number.isInteger(node) ? BigInt(node) : new FloatMarker();
  }
  return node; // string | null
}
