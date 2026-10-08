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
 *   splitRecord(record, contentKeys) -> { content, envelope }   (SPEC.md section 10; reference-only)
 *   decodeInput(tagged) -> CanonValue                            (SPEC.md section 2; test harness)
 *   parseTaggedJson(text) -> TaggedNode                          (SPEC.md section 2; exact numbers)
 *   decodeInputJson(text) -> CanonValue                          (SPEC.md section 2; tagged JSON TEXT)
 */

import { createHash } from "node:crypto";
import { types } from "node:util";

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
// `sign` is informational only: the encoder rejects every InfMarker identically and never reads it.
export class InfMarker {
  constructor(public readonly sign: number) {}
}

// SPEC.md section 2 — a BARE JSON number in a tagged input decodes to an integer only inside
// +/-(2^53 - 1) (D6). JSON.parse silently rounds anything larger (9007199254740993 -> ...992), so
// this port emitted different bytes from Python for the same JSON text with no error. Every port now
// rejects it; larger integers must use a $int tag.
const BARE_INT_MESSAGE = "bare JSON integer beyond +/-(2^53 - 1): use a $int tag";
const MAX_BARE_INT = BigInt(Number.MAX_SAFE_INTEGER);

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
  if (types.isProxy(value)) {
    // A Proxy can report plain data descriptors while its `get` trap returns a different value on
    // every read, so the same value could encode differently twice.
    throw new CanonError("unsupported object for canonical content: Proxy");
  }
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

/**
 * Throw unless `arr` is a plain data array: not a Proxy, prototype Array.prototype, and its own
 * properties are exactly the data indices 0..length-1 plus `length`. A getter on an index was
 * re-evaluated on every encode, an extra named or symbol-keyed property was silently dropped, and a
 * Proxy's `get` trap could return different elements per read — SPEC.md section 1 forbids each.
 */
function assertPlainArray(arr: unknown[]): void {
  if (types.isProxy(arr)) {
    throw new CanonError("unsupported array for canonical content: Proxy");
  }
  if (Object.getPrototypeOf(arr) !== Array.prototype) {
    throw new CanonError("unsupported type for canonical content: array subclass (only plain arrays)");
  }
  if (Object.getOwnPropertySymbols(arr).length > 0) {
    throw new CanonError("unsupported array for canonical content: symbol-keyed property");
  }
  for (let i = 0; i < arr.length; i++) {
    const desc = Object.getOwnPropertyDescriptor(arr, i);
    if (desc === undefined) throw new CanonError("unsupported array for canonical content: sparse array hole");
    if (!("value" in desc)) throw new CanonError("unsupported array for canonical content: accessor element");
  }
  // Every index 0..length-1 is present, so any further own name is a named property.
  if (Object.getOwnPropertyNames(arr).length !== arr.length + 1) {
    throw new CanonError("unsupported array for canonical content: named property on an array");
  }
}

// --------------------------------------------------------------------------- //
// Value dispatch (SPEC.md section 6 — booleans are distinct from numbers in JS).
// --------------------------------------------------------------------------- //

/**
 * Set an OWN data property. `out[k] = v` on a plain `{}` with k === "__proto__" calls the inherited
 * `__proto__` setter instead: a primitive value vanished and an object value replaced the prototype,
 * so `{"__proto__":1}` encoded as `{}` here while Python and Rust keep the key.
 */
function setOwn(out: { [k: string]: CanonValue }, k: string, v: CanonValue): void {
  Object.defineProperty(out, k, { value: v, enumerable: true, writable: true, configurable: true });
}

/**
 * Reject a Proxy before anything else touches the value: `Array.isArray`, `instanceof` and
 * `Object.getPrototypeOf` on a REVOKED Proxy throw a TypeError, which must surface as CanonError.
 */
function rejectProxy(value: unknown): void {
  if (value !== null && (typeof value === "object" || typeof value === "function") && types.isProxy(value)) {
    throw new CanonError("unsupported value for canonical content: Proxy");
  }
}

function encodeValue(value: CanonValue, depth: number): string {
  // `depth` is the number of containers enclosing `value` (0 at the top level).
  rejectProxy(value);
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
  if (Array.isArray(value)) {
    assertPlainArray(value);
    return encodeArray(value, containerDepth(depth));
  }
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
  rejectProxy(value);
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
    if (k !== "digest") setOwn(out, k, (value as { [k: string]: CanonValue })[k]);
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
  rejectProxy(record);
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    throw new CanonError("splitRecord: record must be a plain object");
  }
  assertPlainObject(record);
  const keyset = new Set(contentKeys);
  const content: { [k: string]: CanonValue } = {};
  const envelope: { [k: string]: CanonValue } = {};
  for (const k of Object.keys(record)) {
    setOwn(keyset.has(k) ? content : envelope, k, record[k]);
  }
  return { content, envelope };
}

// --------------------------------------------------------------------------- //
// Type-tagged input decoder (SPEC.md section 2) — identical semantics to Python decode_input.
// --------------------------------------------------------------------------- //

// A parsed tagged-input tree. `bigint` and FloatMarker leaves come from parseTaggedJson, which reads
// each number's SOURCE text; a plain `number` comes from a caller's own JSON.parse.
export type TaggedNode =
  | null
  | boolean
  | number
  | bigint
  | string
  | FloatMarker
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
  return node !== null && typeof node === "object" && !Array.isArray(node) && !(node instanceof FloatMarker);
}

function bareInteger(value: bigint): bigint {
  if (value > MAX_BARE_INT || value < -MAX_BARE_INT) throw new CanonError(BARE_INT_MESSAGE);
  return value;
}

// The decoder reads the caller's tree, so it holds every container to the same plain-data rule as the
// encoder, on the ORIGINAL container and before reading or copying any member: the copy it builds is
// plain, so checking only the copy let an array's named property, an index getter, a Proxy or a
// subclass (also as a `$obj`/`$arr` payload) through decodeInput with no error.
function decodeEntries(p: { [k: string]: TaggedNode }, depth: number): { [k: string]: CanonValue } {
  assertPlainObject(p);
  const inner = containerDepth(depth);
  const out: { [k: string]: CanonValue } = {};
  for (const k of Object.keys(p)) setOwn(out, k, decode(p[k], inner));
  return out;
}

function decodeItems(p: TaggedNode[], depth: number): CanonValue[] {
  assertPlainArray(p);
  const inner = containerDepth(depth);
  const out: CanonValue[] = [];
  for (let i = 0; i < p.length; i++) out.push(decode(p[i], inner));
  return out;
}

function decode(node: TaggedNode, depth: number): CanonValue {
  rejectProxy(node);
  if (isJsonObject(node)) {
    assertPlainObject(node); // before Object.keys / reading the tag payload
    const keys = Object.keys(node);
    if (keys.length === 1) {
      const tag = keys[0];
      const payload = node[tag];
      // Before ANY type check on the payload: Array.isArray, `instanceof`, a `< 0` comparison or
      // getPrototypeOf on a revoked Proxy throws a TypeError (for every tag, `$float`/`$nan` included).
      rejectProxy(payload);
      switch (tag) {
        case "$int":
          return parseIntPayload(payload); // grammar-checked decimal string -> exact bigint
        case "$float":
          return new FloatMarker();
        case "$nan":
          return new NanMarker();
        case "$inf":
          return new InfMarker((typeof payload === "number" || typeof payload === "bigint") && payload < 0 ? -1 : 1);
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
  if (node instanceof FloatMarker) return node; // a fraction/exponent spelling (parseTaggedJson)
  if (typeof node === "boolean") return node;
  if (typeof node === "bigint") return bareInteger(node); // exact, from parseTaggedJson
  if (typeof node === "number") {
    // A bare number from a caller's own JSON.parse (the spelling is gone). Inside +/-(2^53 - 1) it
    // is exact. Any integer-spelled literal past that parses to a double >= 2^53 in magnitude, so the
    // range check rejects every value JSON.parse could have rounded. `-0` is the float -0.0 (as in
    // Rust). A whole-valued fraction or exponent spelling (`1.0`, `1e3`) is indistinguishable from an
    // integer here; parseTaggedJson / decodeInputJson read the source text and reject it.
    if (Object.is(node, -0) || !Number.isInteger(node)) return new FloatMarker();
    if (!Number.isSafeInteger(node)) throw new CanonError(BARE_INT_MESSAGE);
    return BigInt(node);
  }
  return node; // string | null
}

const INTEGER_SPELLING = /^-?(?:0|[1-9][0-9]*)$/;

/**
 * Parse tagged-input JSON TEXT without losing any number (a LOADER, e.g. for a vector corpus whose
 * reject vectors hold out-of-range numbers): an integer spelling becomes an exact `bigint`
 * (range-checked later by decodeInput), and a fraction or exponent spelling (`1.0`, `1e3`) or bare
 * `-0` becomes a FloatMarker. Unlike decodeInputJson it does not reject the text up front, and it sees
 * only the members JSON.parse keeps. It uses JSON.parse source-text access (`context.source`) and
 * fails closed where the runtime lacks it. Invalid or over-deep JSON is a CanonError.
 */
export function parseTaggedJson(text: string): TaggedNode {
  const reviver = (_key: string, value: unknown, context?: { source?: string }): unknown => {
    if (typeof value !== "number") return value;
    const source = context?.source;
    if (typeof source !== "string") {
      throw new CanonError("JSON.parse source-text access is unavailable on this runtime; cannot read bare numbers exactly");
    }
    return INTEGER_SPELLING.test(source) && source !== "-0" ? BigInt(source) : new FloatMarker();
  };
  try {
    return JSON.parse(text, reviver as (key: string, value: unknown) => unknown) as TaggedNode;
  } catch (e) {
    if (e instanceof CanonError) throw e;
    if (e instanceof RangeError) throw new CanonError(DEPTH_MESSAGE); // host parser stack exhausted
    if (e instanceof SyntaxError) throw new CanonError("invalid JSON: " + e.message);
    throw e;
  }
}

const FLOAT_MESSAGE = "floats are forbidden in canonical content; pre-represent as int or string";
const NUMBER_TOKEN_CHAR = /[0-9eE.+-]/;

/**
 * Check EVERY number token in JSON text, in text order — including one that a later duplicate key
 * overwrites, which JSON.parse (and so any reviver) never shows. An integer spelling must lie within
 * +/-(2^53 - 1); a fraction or exponent spelling, or bare `-0`, is a float. Iterative and string-aware;
 * malformed text is left for JSON.parse to reject. Same rule, and messages, as Python's
 * `decode_input_json` hooks and the Rust pre-scan (SPEC.md section 2).
 */
function checkNumberTokens(text: string): void {
  let i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (ch === '"') {
      for (i++; i < n && text[i] !== '"'; i++) if (text[i] === "\\") i++;
      i++;
      continue;
    }
    if (ch === "-" || (ch >= "0" && ch <= "9")) {
      let j = i + 1;
      while (j < n && NUMBER_TOKEN_CHAR.test(text[j])) j++;
      const token = text.slice(i, j);
      if (!INTEGER_SPELLING.test(token) || token === "-0") throw new CanonError(FLOAT_MESSAGE);
      if (token.replace("-", "").length > 16 || !Number.isSafeInteger(Number(token))) {
        throw new CanonError(BARE_INT_MESSAGE);
      }
      i = j;
      continue;
    }
    i++;
  }
}

/**
 * The tagged-JSON TEXT entry point, like Rust `decode_input_json` and Python `decode_input_json`:
 * every number token is checked first (checkNumberTokens), so every number left is an exact safe
 * integer and the host JSON.parse loses nothing; duplicate keys then keep the last member (Rust alone
 * also rejects an overwritten member serde_json cannot parse or that is over-deep: SPEC.md section 2).
 * Invalid or over-deep JSON is a CanonError.
 */
export function decodeInputJson(text: string): CanonValue {
  checkNumberTokens(text);
  let node: TaggedNode;
  try {
    node = JSON.parse(text) as TaggedNode;
  } catch (e) {
    if (e instanceof RangeError) throw new CanonError(DEPTH_MESSAGE); // host parser stack exhausted
    if (e instanceof SyntaxError) throw new CanonError("invalid JSON: " + e.message);
    throw e;
  }
  return decodeInput(node);
}
