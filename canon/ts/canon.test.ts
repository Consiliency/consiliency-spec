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
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  canonicalBytes,
  digest,
  decodeInput,
  CanonError,
  Profile,
} from "./canon.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

// canon v2 is Unicode-DB-INDEPENDENT (NFC moved to the ingestion boundary, which is Python-only).
// There is no Unicode version for this port to assert or report; the pinned-DB / skew coverage lives
// in the Python ingest tests (canon/conformance/test_unicode_skew.py + test_ingest_nfc.py).
const VECTORS = join(HERE, "..", "vectors", "canon-vectors.json");

interface Vector {
  name: string;
  input: unknown;
  profile: Profile;
  expected_canonical_bytes_b64?: string;
  expected_digest_hex?: string;
  expect_error?: boolean;
}

function load(path: string = VECTORS): Vector[] {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function runVector(vec: Vector): [string, string] {
  const value = decodeInput(vec.input as never);
  if (vec.expect_error) {
    try {
      canonicalBytes(value);
    } catch (e) {
      if (e instanceof CanonError) return ["ERROR", "ERROR"];
      throw e;
    }
    throw new Error(`expected CanonError but encoding succeeded for ${vec.name}`);
  }
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

function test(): number {
  const vectors = load();
  const failures: string[] = [];
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
    `TYPESCRIPT CONFORMANCE: PASS (${vectors.length} vectors) [canon v2: Unicode-DB-independent]`,
  );
  return 0;
}

if (process.argv.includes("--emit")) {
  const args = process.argv.slice(2).filter((a) => a !== "--emit");
  emit(args.length ? args[0] : VECTORS);
} else {
  process.exit(test());
}
