/**
 * TypeScript conformance test: run every vector in canon-vectors.json, assert bytes + digest.
 *
 * Usage (via npx tsx):
 *   npx tsx canon/ts/canon.test.ts                # human-readable PASS/FAIL, exit 0/1
 *   npx tsx canon/ts/canon.test.ts --emit          # emit name\tbytes_b64\tdigest for x-lang diff
 *   npx tsx canon/ts/canon.test.ts --emit <corpus> # emit over an alternate corpus (e.g. the cross-repo
 *                                                   # downstream-consumer vectors); the emit is the canon v2
 *                                                   # REFERENCE, used to hold every other v2 engine
 *                                                   # (Rust core, PyO3, WASM) byte-identical over that domain.
 *   npx tsx canon/ts/canon.test.ts --emit-boundary # emit name\tOK|ERROR\tbytes_b64\tdigest over the engine
 *                                                   # boundary vectors, for cross-engine diffs
 *
 * The self-test also runs ../conformance/engine_boundary_vectors.json (CAN-13) and
 * the native-value checks JSON cannot carry: non-plain objects (CAN-1) and the nesting cap (CAN-12).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  canonicalBytes,
  digest,
  decodeInput,
  decodeInputJson,
  parseTaggedJson,
  CanonError,
  CanonValue,
  MAX_DEPTH,
  Profile,
  splitRecord,
} from "./canon.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

// canon v2 is Unicode-DB-INDEPENDENT (NFC moved to the ingestion boundary, which is Python-only).
// There is no Unicode version for this port to assert or report; the pinned-DB / skew coverage lives
// in the Python ingest tests (canon/conformance/test_unicode_skew.py + test_ingest_nfc.py).
const VECTORS = join(HERE, "..", "vectors", "canon-vectors.json");
const BOUNDARY = join(HERE, "..", "conformance", "engine_boundary_vectors.json");
// Boundary vectors for behaviour no PUBLISHED canon-core has yet: run only against the in-tree engines
// (see test_canon.py). Fold into BOUNDARY when the published gate is repinned. Empty since the
// canon-core 0.4.0 repin, which folded duplicate-key-f1..f6 into BOUNDARY.
const BOUNDARY_NEXT = join(HERE, "..", "conformance", "engine_boundary_vectors_next.json");

// Count guards (CAN-15): a truncated local corpus must fail, not pass on whatever is left. Floors,
// raised as the corpus grows (published vectors are never removed). Same values as test_canon.py.
const MIN_VALID = 44;
const MIN_ERROR = 37;
const MIN_BOUNDARY = 26;
const MIN_BOUNDARY_NEXT = 0;

interface BoundaryVector {
  name: string;
  input?: unknown;
  raw?: string;
  expect: "accept" | "reject";
  bytes?: string;
}

interface Vector {
  name: string;
  input: unknown;
  profile: Profile;
  expected_canonical_bytes_b64?: string;
  expected_digest_hex?: string;
  expect_error?: boolean;
}

function load(path: string = VECTORS): Vector[] {
  // parseTaggedJson, not JSON.parse: every bare number in a vector input keeps its exact value and
  // spelling (JSON.parse would round 9007199254740993 and turn 1.0 into 1 before decodeInput runs).
  return parseTaggedJson(readFileSync(path, "utf-8")) as unknown as Vector[];
}

function runVector(vec: Vector): [string, string] {
  if (vec.expect_error) {
    // decodeInput is INSIDE the guard: a $int payload-grammar rejection is thrown at decode.
    // Only CanonError counts; any other error type is a real failure and propagates.
    try {
      canonicalBytes(decodeInput(vec.input as never));
    } catch (e) {
      if (e instanceof CanonError) return ["ERROR", "ERROR"];
      throw e;
    }
    throw new Error(`expected CanonError but encoding succeeded for ${vec.name}`);
  }
  const value = decodeInput(vec.input as never);
  const cbytes = canonicalBytes(value);
  const b64 = Buffer.from(cbytes).toString("base64");
  return [b64, digest(value, vec.profile)];
}

function emit(path: string = VECTORS): void {
  // One TAB-separated line per vector (name, bytes_b64, digest), sorted by name. Plain string
  // concatenation only -- NO JSON.stringify -- so the cross-language diff compares canon output
  // itself, not an incidental JSON-pretty-printing difference. Mirrors py/test_canon.py emit().
  const lines: string[] = [];
  for (const vec of load(path)) {
    const [b64, dig] = runVector(vec);
    lines.push(`${vec.name}\t${b64}\t${dig}`);
  }
  process.stdout.write(lines.sort().join("\n") + "\n");
}

function runBoundary(bv: BoundaryVector): [string, string, string] {
  // `raw` vectors are JSON text and go through the text entry point decodeInputJson (exact bare
  // numbers; host-parser stack exhaustion on over-deep text is a CanonError there); `input` vectors
  // are tagged trees. Only CanonError counts as rejection.
  let value: CanonValue;
  let cbytes: Uint8Array;
  try {
    value = bv.raw !== undefined ? decodeInputJson(bv.raw) : decodeInput(bv.input as never);
    cbytes = canonicalBytes(value);
  } catch (e) {
    if (e instanceof CanonError) return ["ERROR", "-", "-"];
    throw e;
  }
  return ["OK", Buffer.from(cbytes).toString("base64"), digest(value, "semantic-content")];
}

function loadBoundary(path: string = BOUNDARY): BoundaryVector[] {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function emitBoundary(path: string = BOUNDARY): void {
  const lines = loadBoundary(path).map((bv) => [bv.name, ...runBoundary(bv)].join("\t"));
  process.stdout.write(lines.sort().join("\n") + "\n");
}

function boundaryFailures(path: string = BOUNDARY, minimum: number = MIN_BOUNDARY): string[] {
  const failures: string[] = [];
  const vectors = loadBoundary(path);
  if (vectors.length < minimum) {
    failures.push(`${path} has ${vectors.length} vectors (< ${minimum}); it looks truncated`);
  }
  for (const bv of vectors) {
    const [status, b64] = runBoundary(bv);
    if (bv.expect === "reject") {
      if (status !== "ERROR") failures.push(`boundary ${bv.name}: expected CanonError, accepted`);
      continue;
    }
    if (status !== "OK") {
      failures.push(`boundary ${bv.name}: expected acceptance, rejected`);
      continue;
    }
    const got = Buffer.from(b64, "base64").toString("utf8");
    if (typeof bv.bytes === "string" && got !== bv.bytes) {
      failures.push(`boundary ${bv.name}: bytes ${JSON.stringify(got)} != ${JSON.stringify(bv.bytes)}`);
    }
  }
  return failures;
}

function expectCanonError(label: string, fn: () => unknown, failures: string[]): void {
  try {
    fn();
  } catch (e) {
    if (!(e instanceof CanonError)) failures.push(`${label}: expected CanonError, got ${String(e)}`);
    return;
  }
  failures.push(`${label}: expected CanonError, accepted`);
}

function nativeFailures(): string[] {
  const failures: string[] = [];
  // CAN-1: SPEC section 1 requires language-native types to be rejected, never coerced. A Date used to
  // encode as {} and a class instance as its own fields. JSON cannot carry these, so they live here.
  class Point {
    x = 1n;
  }
  const nonPlain: Array<[string, unknown]> = [
    ["Date", new Date(0)],
    ["Map", new Map([["a", 1n]])],
    ["Set", new Set([1n])],
    ["RegExp", /x/],
    ["Uint8Array", new Uint8Array([1, 2])],
    ["class instance", new Point()],
    ["boxed String", new String("x")],
    ["sparse array hole", [1n, , 2n]],
    ["undefined", undefined],
  ];
  for (const [label, v] of nonPlain) {
    expectCanonError(`CAN-1 ${label}`, () => canonicalBytes({ k: v } as never), failures);
  }
  // CAN-1 through the public digest() and splitRecord() APIs. digest() strips a top-level `digest`
  // key by copying into a fresh plain object; that copy used to launder a Date/Map/class instance
  // carrying an own `digest` property into `{}` or its own fields (so it digested while
  // canonicalBytes and Python rejected it).
  class Rec {
    x = 1n;
    digest = "old";
  }
  const withDigest = <T extends object>(v: T): T => Object.assign(v, { digest: "old" });
  const viaDigest: Array<[string, unknown]> = [
    ["Date with digest", withDigest(new Date(0))],
    ["Map with digest", withDigest(new Map([["k", 1n]]))],
    ["class instance with digest", new Rec()],
    ["class instance without digest", new Point()],
  ];
  for (const [label, v] of viaDigest) {
    expectCanonError(`CAN-1 digest(${label})`, () => digest(v as never, "run"), failures);
    expectCanonError(`CAN-1 splitRecord(${label})`, () => splitRecord(v as never, ["x"]), failures);
  }
  // The plain case keeps working: a top-level digest key is excluded from its own digest.
  if (digest({ x: 1n, digest: "old" } as never, "run") !== digest({ x: 1n }, "run")) {
    failures.push("plain object: top-level digest key not excluded");
  }
  const split = splitRecord({ x: 1n, at: "now" }, ["x"]);
  if (JSON.stringify(Object.keys(split.content)) !== '["x"]' || JSON.stringify(Object.keys(split.envelope)) !== '["at"]') {
    failures.push("splitRecord: plain record split changed");
  }
  // Plain-prototype objects that are still not plain DATA: an accessor (re-evaluated per encode), a
  // symbol-keyed or a non-enumerable property (silently dropped by Object.keys). All rejected.
  const accessor = Object.defineProperty({ a: 1n }, "b", { get: () => 2n, enumerable: true });
  const symbolKeyed = Object.assign({ a: 1n }, { [Symbol("s")]: 1n });
  const hidden = Object.defineProperty({ a: 1n }, "b", { value: 2n, enumerable: false });
  for (const [label, v] of [["accessor", accessor], ["symbol key", symbolKeyed], ["non-enumerable", hidden]] as const) {
    expectCanonError(`canonicalBytes(${label})`, () => canonicalBytes(v as never), failures);
    expectCanonError(`digest(${label} + digest key)`, () => digest(withDigest(v as object) as never, "run"), failures);
  }

  // A null-prototype object IS plain data and must keep encoding.
  const bare = Object.assign(Object.create(null), { b: 2n, a: 1n });
  const got = new TextDecoder().decode(canonicalBytes(bare));
  if (got !== '{"a":1,"b":2}') failures.push(`null-prototype object: got ${got}`);

  // CAN-12: the cap applies to native values; far past it is a CanonError, never a RangeError.
  if (MAX_DEPTH !== 128) failures.push(`MAX_DEPTH is ${MAX_DEPTH}, expected 128`);
  const nested = (depth: number): CanonValue => {
    let v: CanonValue = [];
    for (let i = 1; i < depth; i++) v = [v];
    return v;
  };
  try {
    canonicalBytes(nested(MAX_DEPTH));
  } catch (e) {
    failures.push(`native depth ${MAX_DEPTH}: expected acceptance, got ${String(e)}`);
  }
  for (const depth of [MAX_DEPTH + 1, 100000]) {
    expectCanonError(`native canonicalBytes depth ${depth}`, () => canonicalBytes(nested(depth)), failures);
    expectCanonError(`native digest depth ${depth}`, () => digest(nested(depth), "run"), failures);
    expectCanonError(`decodeInput depth ${depth}`, () => decodeInput(nested(depth) as never), failures);
  }

  // Arrays and Proxies are held to the plain-data rule too. An extra named property used to be
  // silently dropped, and an index getter or a Proxy `get` trap gave different bytes per encode.
  const named = Object.assign([1n, 2n], { extra: 3n });
  let reads = 0;
  const indexGetter = Object.defineProperty([0n], 0, { get: () => BigInt(++reads), enumerable: true });
  let proxyReads = 0;
  const proxyArray = new Proxy([0n], { get: (t, k, r) => (k === "0" ? BigInt(++proxyReads) : Reflect.get(t, k, r)) });
  const proxyObject = new Proxy({ a: 1n }, {});
  class MyArray extends Array {}
  const subclass = MyArray.from([1n]);
  const symbolArray = Object.assign([1n], { [Symbol("s")]: 1n });
  const arrays: Array<[string, unknown]> = [
    ["array with a named property", named],
    ["array index getter", indexGetter],
    ["Proxy array", proxyArray],
    ["Proxy object", proxyObject],
    ["array subclass", subclass],
    ["array with a symbol key", symbolArray],
  ];
  for (const [label, v] of arrays) {
    expectCanonError(`canonicalBytes(${label})`, () => canonicalBytes(v as never), failures);
    expectCanonError(`canonicalBytes({k: ${label}})`, () => canonicalBytes({ k: v } as never), failures);
    expectCanonError(`digest(${label})`, () => digest(v as never, "run"), failures);
  }
  expectCanonError("splitRecord(Proxy object)", () => splitRecord(proxyObject as never, ["a"]), failures);
  // The decoder holds the ORIGINAL tree to the same rule before copying it (its copy is plain, so
  // checking only the copy let every one of these through decodeInput), bare and as a tag payload.
  for (const [label, v] of arrays) {
    expectCanonError(`decodeInput(${label})`, () => canonicalBytes(decodeInput(v as never)), failures);
    expectCanonError(`decodeInput({k: ${label}})`, () => canonicalBytes(decodeInput({ k: v } as never)), failures);
    if (Array.isArray(v)) {
      expectCanonError(`decodeInput({$arr: ${label}})`, () => canonicalBytes(decodeInput({ $arr: v } as never)), failures);
    } else {
      expectCanonError(`decodeInput({$obj: ${label}})`, () => canonicalBytes(decodeInput({ $obj: v } as never)), failures);
    }
  }
  const getterObject = Object.defineProperty({}, "a", { get: () => 1, enumerable: true });
  expectCanonError("decodeInput(object with an accessor)", () => decodeInput(getterObject as never), failures);
  // A REVOKED Proxy is a CanonError on every entry point, not the TypeError Array.isArray throws.
  for (const target of [[1n], { a: 1n }]) {
    const { proxy, revoke } = Proxy.revocable(target as object, {});
    revoke();
    expectCanonError("canonicalBytes(revoked Proxy)", () => canonicalBytes(proxy as never), failures);
    expectCanonError("digest(revoked Proxy)", () => digest(proxy as never, "run"), failures);
    expectCanonError("splitRecord(revoked Proxy)", () => splitRecord(proxy as never, ["a"]), failures);
    expectCanonError("decodeInput(revoked Proxy)", () => decodeInput(proxy as never), failures);
    expectCanonError("canonicalBytes({k: revoked Proxy})", () => canonicalBytes({ k: proxy } as never), failures);
    // As a tag payload (every tag, since the payload is guarded before any type check) and nested.
    for (const tag of ["$arr", "$obj", "$int", "$str", "$bool", "$null", "$inf", "$float", "$nan"]) {
      expectCanonError(`decodeInput({${tag}: revoked Proxy})`, () => decodeInput({ [tag]: proxy } as never), failures);
    }
    const nests: Array<[string, unknown]> = [
      ["$arr element", { $arr: [1, proxy] }],
      ["$obj member", { $obj: { a: { $arr: [proxy] } } }],
      ["bare array element", [[proxy]]],
      ["bare object member", { a: { b: proxy } }],
    ];
    for (const [label, v] of nests) {
      expectCanonError(`decodeInput(revoked Proxy as ${label})`, () => decodeInput(v as never), failures);
      expectCanonError(`canonicalBytes(revoked Proxy as ${label})`, () => canonicalBytes(v as never), failures);
      expectCanonError(`digest(revoked Proxy as ${label})`, () => digest(v as never, "run"), failures);
    }
    expectCanonError("splitRecord(record holding a revoked Proxy) + encode", () => {
      const parts = splitRecord({ a: proxy } as never, ["a"]);
      canonicalBytes(parts.content);
    }, failures);
  }
  // `__proto__` is an ordinary own key on every path (it used to go through the inherited setter).
  for (const [text, want] of [
    ['{"__proto__":1}', '{"__proto__":1}'],
    ['{"__proto__":{"a":1},"b":2}', '{"__proto__":{"a":1},"b":2}'],
    ['{"$obj":{"__proto__":{"$int":"5"}}}', '{"__proto__":5}'],
  ]) {
    const got = new TextDecoder().decode(canonicalBytes(decodeInputJson(text)));
    if (got !== want) failures.push(`decodeInputJson(${text}): got ${got}`);
  }
  const ownProto = JSON.parse('{"__proto__":1,"digest":"x","b":2}', (_k, v) => (typeof v === "number" ? BigInt(v) : v));
  const protoDigest = digest(ownProto, "run");
  const expected = digest(decodeInputJson('{"__proto__":1,"b":2}'), "run");
  if (protoDigest !== expected) failures.push("digest(): own __proto__ key lost by the top-level digest strip");
  const parts = splitRecord(ownProto, ["__proto__"]);
  if (new TextDecoder().decode(canonicalBytes(parts.content)) !== '{"__proto__":1}') {
    failures.push("splitRecord(): own __proto__ key lost");
  }
  // A plain array (including an empty one and a frozen one) keeps encoding.
  const plain = new TextDecoder().decode(canonicalBytes([1n, [], Object.freeze([2n])] as never));
  if (plain !== "[1,[],[2]]") failures.push(`plain arrays: got ${plain}`);

  // D6: bare numbers. The text entry point reads each number's spelling; a caller's own JSON.parse
  // tree applies the same range rule (every rounded literal lands at or past 2^53).
  const bytesOf = (v: CanonValue): string => new TextDecoder().decode(canonicalBytes(v));
  for (const [text, want] of [
    ["9007199254740991", "9007199254740991"],
    ["[-9007199254740991,0]", "[-9007199254740991,0]"],
    ['{"$int":"9007199254740993"}', "9007199254740993"],
  ]) {
    const got = bytesOf(decodeInputJson(text));
    if (got !== want) failures.push(`decodeInputJson(${text}): got ${got}`);
  }
  for (const text of ["9007199254740992", "-9007199254740992", "9007199254740993", '{"n":18446744073709551616}',
    "-0", "1.0", "1e3", "10E-1", "[1,", "NaN", "[" + "[".repeat(20000) + "]".repeat(20000) + "]",
    '{"n":10000000000000000,"n":0}', '{"n":9007199254740992,"n":0}', '{"n":1.0,"n":1}', "9007199254740992.0", "1e300"]) {
    expectCanonError(`decodeInputJson(${text.slice(0, 20)})`, () => canonicalBytes(decodeInputJson(text)), failures);
  }
  for (const n of [2 ** 53, -(2 ** 53), 9007199254740993, -0, 1.5]) {
    expectCanonError(`decodeInput(bare ${n})`, () => canonicalBytes(decodeInput({ n } as never)), failures);
  }
  if (bytesOf(decodeInput([9007199254740991, -9007199254740991] as never)) !== "[9007199254740991,-9007199254740991]") {
    failures.push("decodeInput: safe bare integers no longer decode");
  }
  return failures;
}

function test(): number {
  const vectors = load();
  const failures: string[] = [
    ...boundaryFailures(),
    ...boundaryFailures(BOUNDARY_NEXT, MIN_BOUNDARY_NEXT),
    ...nativeFailures(),
  ];
  const nError = vectors.filter((v) => v.expect_error).length;
  if (vectors.length - nError < MIN_VALID || nError < MIN_ERROR) {
    failures.push(
      `corpus has ${vectors.length - nError} valid / ${nError} reject vectors (< ${MIN_VALID} / ${MIN_ERROR}); it looks truncated`,
    );
  }
  for (const vec of vectors) {
    const [b64, dig] = runVector(vec);
    if (vec.expect_error) {
      if (b64 !== "ERROR" || dig !== "ERROR") {
        failures.push(`${vec.name}: expected error, got ${b64}`);
      }
      continue;
    }
    if (b64 !== vec.expected_canonical_bytes_b64) {
      failures.push(
        `${vec.name}: bytes mismatch\n  expected ${vec.expected_canonical_bytes_b64}\n  got      ${b64}`,
      );
    }
    if (dig !== vec.expected_digest_hex) {
      failures.push(
        `${vec.name}: digest mismatch\n  expected ${vec.expected_digest_hex}\n  got      ${dig}`,
      );
    }
  }
  if (failures.length) {
    console.log(`TYPESCRIPT CONFORMANCE: FAIL (${failures.length})`);
    for (const f of failures) console.log("  - " + f);
    return 1;
  }
  console.log(
    `TYPESCRIPT CONFORMANCE: PASS (${vectors.length} vectors, ${loadBoundary().length}+${loadBoundary(BOUNDARY_NEXT).length} boundary vectors, ` +
      `native CAN-1/depth checks) [canon v2: Unicode-DB-independent]`,
  );
  return 0;
}

if (process.argv.includes("--emit-boundary")) {
  const args = process.argv.slice(2).filter((a) => a !== "--emit-boundary");
  emitBoundary(args.length ? args[0] : BOUNDARY);
} else if (process.argv.includes("--emit")) {
  const args = process.argv.slice(2).filter((a) => a !== "--emit");
  emit(args.length ? args[0] : VECTORS);
} else {
  process.exit(test());
}
