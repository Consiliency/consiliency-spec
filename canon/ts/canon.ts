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

// SPEC.md section 1 — maximum nesting depth (CAN-12). A container's depth is
// 1 + the number of containers enclosing it (`[]` is depth 1). Any container deeper than MAX_DEPTH
// is a CanonError, at decode and at encode, identically in every port. Without it this port threw a
// RangeError at a non-deterministic depth (2,000-5,000) while Python and Rust failed elsewhere.
export const MAX_DEPTH = 128;
const DEPTH_MESSAGE = `nesting depth exceeds the maximum of ${MAX_DEPTH}`;

function containerDepth(depth: number): number {
  // `depth` counts the containers enclosing the one being built; returns the new container's depth.
  if (depth >= MAX_DEPTH) throw new CanonError(DEPTH_MESSAGE);
  return depth + 1;
}

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

// Supported canonical value domain (SPEC.md section 1). Integers are `bigint`. A plain JS `number`
// is NOT a canonical value and is rejected by the encoder: JS cannot distinguish `100` from `100.0`,
// so a whole-valued float would silently encode as the integer `100` while every other port rejects
// it — a cross-port digest divergence. Callers convert with `BigInt(...)` at their own boundary,
// where they know whether the value was an integer.
export type CanonValue =
  | null
  | boolean
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

function encodeNumber(value: bigint): string {
  return value.toString(10);
}

// The one payload grammar every port shares (SPEC.md section 2): an optional '-' then one or more
// ASCII digits. Nothing else — not whitespace, '+', '_', '0x', or non-ASCII digits, all of which
// `BigInt(string)` / Python `int()` / Rust `str::parse` accept DIFFERENTLY from one another.
// Leading zeros and "-0" match the grammar and normalise through bigint ("007" -> 7, "-0" -> 0).
const INT_PAYLOAD = /^-?[0-9]+$/;

// The message is FIXED: it never interpolates the payload, so a rejected value cannot leak through
// the error channel (the C-ABI forwards messages to callers verbatim).
export function parseIntPayload(payload: unknown): bigint {
  if (typeof payload !== "string" || !INT_PAYLOAD.test(payload)) {
    throw new CanonError("invalid $int payload: expected optional '-' then ASCII digits");
  }
  return BigInt(payload);
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

function encodeObject(obj: { [k: string]: CanonValue }, depth: number): string {
  // canon v2 does NOT NFC-normalize keys (the ingest boundary did that, and detected post-NFC
  // collisions). canon sorts the already-NFC keys by Unicode code point as-is.
  const keys = Object.keys(obj).sort(compareCodePoints);
  const parts: string[] = [];
  for (const k of keys) {
    parts.push(encodeString(k) + ":" + encodeValue(obj[k], depth));
  }
  return "{" + parts.join(",") + "}";
}

function encodeArray(arr: CanonValue[], depth: number): string {
  // Insertion order preserved ALWAYS (SPEC.md section 4). Never sort. An index loop, not .map():
  // .map() skips the holes of a sparse array and join() renders them as empty, which emitted
  // invalid output like `[1,,2]`; here a hole reads as `undefined` and is rejected.
  const parts: string[] = [];
  for (let i = 0; i < arr.length; i++) parts.push(encodeValue(arr[i], depth));
  return "[" + parts.join(",") + "]";
}

/**
 * A plain data object: its prototype is Object.prototype or null. Everything else that is
 * `typeof "object"` (Date, Map, Set, RegExp, typed arrays, class instances, boxed primitives) is a
 * language-native type SPEC.md section 1 requires to be REJECTED, never coerced. Before this check a
 * Date encoded as `{}` and a class instance as its own enumerable fields (CAN-1),
 * while Python rejects every non-dict.
 */
function isPlainObject(value: object): boolean {
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * Throw unless `value` is plain data: a plain object (see isPlainObject) whose own properties are all
 * enumerable, string-keyed DATA properties. An accessor would be re-evaluated on every encode (the
 * same object could encode differently twice), and a symbol-keyed or non-enumerable property would be
 * silently dropped by Object.keys — both are coercions SPEC.md section 1 forbids. Called BEFORE any
 * copy of the object is made (digest's top-level `digest` strip, splitRecord), so a copy can never
 * launder a non-plain value into a plain one.
 */
function assertPlainObject(value: object): void {
  if (!isPlainObject(value)) {
    const ctor = (value as { constructor?: { name?: unknown } }).constructor;
    const name = typeof ctor?.name === "string" && ctor.name ? ctor.name : "object";
    throw new CanonError("unsupported type for canonical content: " + name + " (only plain objects)");
  }
  if (Object.getOwnPropertySymbols(value).length > 0) {
    throw new CanonError("unsupported object for canonical content: symbol-keyed property");
  }
  const names = Object.getOwnPropertyNames(value);
  if (names.length !== Object.keys(value).length) {
    throw new CanonError("unsupported object for canonical content: non-enumerable property");
  }
  for (const k of names) {
    const desc = Object.getOwnPropertyDescriptor(value, k);
    if (desc === undefined || !("value" in desc)) {
      throw new CanonError("unsupported object for canonical content: accessor property");
    }
  }
}

// --------------------------------------------------------------------------- //
// Value dispatch (SPEC.md section 6 — booleans are distinct from numbers in JS).
// --------------------------------------------------------------------------- //

function encodeValue(value: CanonValue, depth: number): string {
  // `depth` is the number of containers enclosing `value` (0 at the top level).
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
  if (typeof value === "bigint") return encodeNumber(value);
  if (typeof value === "number") {
    // Rejected outright (not routed by integrality): see the CanonValue comment. A float marker is
    // the vector-harness representation; a raw number reaching the encoder is a caller bug.
    throw new CanonError(
      "plain JS number is not a canonical value: use bigint for integers (floats are forbidden)"
    );
  }
  if (typeof value === "string") return encodeString(value);
  if (Array.isArray(value)) return encodeArray(value, containerDepth(depth));
  if (typeof value === "object") {
    assertPlainObject(value);
    return encodeObject(value as { [k: string]: CanonValue }, containerDepth(depth));
  }
  throw new CanonError("unsupported type for canonical content: " + typeof value);
}

// --------------------------------------------------------------------------- //
// Public API (SPEC.md sections 8, 9)
// --------------------------------------------------------------------------- //

export function canonicalBytes(value: CanonValue): Uint8Array {
  return new TextEncoder().encode(encodeValue(value, 0));
}

function stripTopLevelDigest(value: CanonValue): CanonValue {
  // SPEC.md section 8: exclude a top-level "digest" key only.
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    value instanceof FloatMarker ||
    value instanceof NanMarker ||
    value instanceof InfMarker
  ) {
    return value; // canonicalBytes accepts or rejects it as-is
  }
  // Check the ORIGINAL object before copying it: the copy below is a fresh plain `{}`, so a Date,
  // Map or class instance carrying an own `digest` key used to be laundered into a plain object and
  // digested as `{}` or as its own fields, while canonicalBytes and Python reject it (CAN-1).
  assertPlainObject(value);
  if (!Object.prototype.hasOwnProperty.call(value, "digest")) return value;
  const out: { [k: string]: CanonValue } = {};
  for (const k of Object.keys(value as { [k: string]: CanonValue })) {
    if (k !== "digest") out[k] = (value as { [k: string]: CanonValue })[k];
  }
  return out;
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
  // Same copy-before-check hazard as the digest strip: content/envelope are fresh plain objects, so
  // the record itself must be plain data before its fields are copied out.
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    throw new CanonError("splitRecord: record must be a plain object");
  }
  assertPlainObject(record);
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

/**
 * Every tag's payload has exactly one accepted JSON type (SPEC.md section 2); anything else is a
 * fixed-message CanonError, never a truthiness or iteration coercion. Containers deeper than
 * MAX_DEPTH are rejected here as well as in the encoder (SPEC.md section 1).
 */
export function decodeInput(node: TaggedNode): CanonValue {
  return decode(node, 0);
}

function isJsonObject(node: TaggedNode): node is { [k: string]: TaggedNode } {
  return node !== null && typeof node === "object" && !Array.isArray(node);
}

function decodeEntries(p: { [k: string]: TaggedNode }, depth: number): { [k: string]: CanonValue } {
  const inner = containerDepth(depth);
  const out: { [k: string]: CanonValue } = {};
  for (const k of Object.keys(p)) out[k] = decode(p[k], inner);
  return out;
}

function decodeItems(p: TaggedNode[], depth: number): CanonValue[] {
  const inner = containerDepth(depth);
  const out: CanonValue[] = [];
  for (let i = 0; i < p.length; i++) out.push(decode(p[i], inner));
  return out;
}

function decode(node: TaggedNode, depth: number): CanonValue {
  if (isJsonObject(node)) {
    const keys = Object.keys(node);
    if (keys.length === 1) {
      const tag = keys[0];
      const payload = node[tag];
      switch (tag) {
        case "$int":
          return parseIntPayload(payload); // grammar-checked decimal string -> exact bigint
        case "$float":
          return new FloatMarker();
        case "$nan":
          return new NanMarker();
        case "$inf":
          return new InfMarker(typeof payload === "number" && payload < 0 ? -1 : 1);
        case "$str":
          if (typeof payload !== "string") {
            throw new CanonError("invalid $str payload: expected a JSON string");
          }
          return payload;
        case "$bool":
          // Boolean(payload) made [] and {} true here while Python made them false and Rust made
          // 0.0 true (CAN-9). The payload must BE a boolean.
          if (typeof payload !== "boolean") {
            throw new CanonError("invalid $bool payload: expected a JSON boolean");
          }
          return payload;
        case "$null":
          if (payload !== true) throw new CanonError("invalid $null payload: expected true");
          return null;
        case "$obj":
          if (!isJsonObject(payload)) {
            throw new CanonError("invalid $obj payload: expected a JSON object");
          }
          return decodeEntries(payload, depth);
        case "$arr":
          if (!Array.isArray(payload)) {
            throw new CanonError("invalid $arr payload: expected a JSON array");
          }
          return decodeItems(payload, depth);
        default:
          break; // one-key object that is not a tag: fall through
      }
    }
    return decodeEntries(node, depth);
  }
  if (Array.isArray(node)) return decodeItems(node, depth);
  if (typeof node === "boolean") return node;
  if (typeof node === "number") {
    // A bare JSON number in a vector input: integral -> bigint; fractional -> float marker. This is
    // a VECTOR-HARNESS convenience for untagged fixtures only, not the public API — the encoder
    // itself rejects plain numbers (see encodeValue). JSON.parse has already turned `1.0` into `1`,
    // which is why type intent is carried by tags (SPEC.md section 2).
    return Number.isInteger(node) ? BigInt(node) : new FloatMarker();
  }
  return node; // string | null
}
