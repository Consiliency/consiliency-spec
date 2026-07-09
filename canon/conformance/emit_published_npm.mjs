/**
 * Emit + ASSERT canon output through the PUBLISHED npm package `@consiliency/canon-core`.
 *
 * This is the GATE-phase companion to emit_wasm.mjs. emit_wasm.mjs loads a WASM binding BUILT FROM
 * SOURCE in the working tree; THIS harness `require`s the package a JS consumer actually installs from
 * the npm registry (resolved from node_modules, cwd = the temp install dir the orchestrator created).
 * The published tarball is a SEPARATE code path from the source build — a wrong `files` list, a stale
 * corpus, a bad wasm-pack output would all be invisible to the source-built gate but caught here.
 *
 * The oracle is the corpus SHIPPED INSIDE the package (`@consiliency/canon-core/canon-vectors.json`):
 * every valid vector's emitted bytes(b64)+digest(hex) MUST equal that vector's expected_* fields, and
 * every expect_error vector MUST reject. Count guards fail-closed against a truncated corpus.
 *
 * It also runs the shared engine-level BOUNDARY vectors (engine_boundary_vectors.json): the tag-vs-
 * literal-$int-key razor, 2^53+/-1 exact big integers, and nesting past the serde recursion ceiling
 * (which MUST reject, not mis-digest).
 *
 * Usage:  node emit_published_npm.mjs <corpus.json> <boundary.json> <out_corpus.txt> <out_boundary.txt>
 *   cwd MUST contain node_modules/@consiliency/canon-core (the orchestrator installs it there).
 * Exit 0 = all assertions hold. Exit 1 = a divergence (bytes/digest/accept/reject/count) — gate fail.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

// Resolve @consiliency/canon-core from the CWD's node_modules (the orchestrator installs the pinned
// published package into a temp dir and runs this harness with cwd = that dir), NOT from this script's
// location under canon/conformance/. Basing createRequire on cwd/package.json makes node's resolver
// walk the temp install dir's node_modules.
const require = createRequire(pathToFileURL(path.join(process.cwd(), "package.json")));

const [corpusPath, boundaryPath, outCorpus, outBoundary] = process.argv.slice(2);
if (!corpusPath || !boundaryPath || !outCorpus || !outBoundary) {
  console.error("usage: node emit_published_npm.mjs <corpus.json> <boundary.json> <out_corpus.txt> <out_boundary.txt>");
  process.exit(2);
}

// The REAL published artifact — resolved from node_modules of the orchestrator's temp install dir.
const canon = require("@consiliency/canon-core");
const { canonicalBytesFromJson, digestFromJson } = canon;
const MIN_VALID = 30;
const MIN_ERROR = 6;

let failures = 0;
const fail = (msg) => { console.error(`FAIL: ${msg}`); failures++; };

// --- Corpus vectors: assert against the package-shipped oracle (expected_* fields) ----------------
const vectors = JSON.parse(readFileSync(corpusPath, "utf8"));
let valid = 0;
let errors = 0;
const corpusLines = [];
for (const v of vectors) {
  const tagged = JSON.stringify(v.input); // ASCII-safe: JSON.stringify escapes a lone surrogate as \udXXX
  if (v.expect_error) {
    errors++;
    try {
      canonicalBytesFromJson(tagged);
      fail(`vector ${v.name}: expected rejection but the PUBLISHED npm engine accepted it`);
      corpusLines.push(`${v.name}\tACCEPTED-BUG\tACCEPTED-BUG`);
    } catch {
      corpusLines.push(`${v.name}\tERROR\tERROR`);
    }
    continue;
  }
  valid++;
  const b64 = Buffer.from(canonicalBytesFromJson(tagged)).toString("base64");
  const dig = digestFromJson(tagged, v.profile);
  if (b64 !== v.expected_canonical_bytes_b64) {
    fail(`vector ${v.name}: bytes ${b64} != oracle ${v.expected_canonical_bytes_b64}`);
  }
  if (dig !== v.expected_digest_hex) {
    fail(`vector ${v.name}: digest ${dig} != oracle ${v.expected_digest_hex}`);
  }
  corpusLines.push(`${v.name}\t${b64}\t${dig}`);
}
if (valid < MIN_VALID) fail(`only ${valid} valid vectors (< ${MIN_VALID}); corpus looks truncated`);
if (errors < MIN_ERROR) fail(`only ${errors} expect_error vectors (< ${MIN_ERROR}); corpus looks truncated`);
corpusLines.sort();
writeFileSync(outCorpus, corpusLines.join("\n") + "\n");

// --- Engine-level boundary vectors ----------------------------------------------------------------
const boundary = JSON.parse(readFileSync(boundaryPath, "utf8"));
const boundaryLines = [];
for (const bv of boundary) {
  const tagged = Object.prototype.hasOwnProperty.call(bv, "raw") ? bv.raw : JSON.stringify(bv.input);
  if (bv.expect === "reject") {
    try {
      canonicalBytesFromJson(tagged);
      fail(`boundary ${bv.name}: expected rejection but the PUBLISHED npm engine accepted it`);
      boundaryLines.push(`${bv.name}\tACCEPTED-BUG\t-\t-`);
    } catch {
      boundaryLines.push(`${bv.name}\tERROR\t-\t-`);
    }
    continue;
  }
  // expect accept
  let b64;
  let dig;
  try {
    const out = canonicalBytesFromJson(tagged);
    b64 = Buffer.from(out).toString("base64");
    dig = digestFromJson(tagged, "semantic-content");
    if (typeof bv.bytes === "string" && Buffer.from(out).toString("utf8") !== bv.bytes) {
      fail(`boundary ${bv.name}: canonical bytes ${JSON.stringify(Buffer.from(out).toString("utf8"))} != expected ${JSON.stringify(bv.bytes)}`);
    }
  } catch (e) {
    fail(`boundary ${bv.name}: expected acceptance but the PUBLISHED npm engine rejected it (${String(e.message || e).slice(0, 80)})`);
    b64 = "REJECTED-BUG";
    dig = "REJECTED-BUG";
  }
  boundaryLines.push(`${bv.name}\tOK\t${b64}\t${dig}`);
}
boundaryLines.sort();
writeFileSync(outBoundary, boundaryLines.join("\n") + "\n");

console.log(`npm @consiliency/canon-core :: corpus valid=${valid} error=${errors}, boundary=${boundary.length}, failures=${failures}`);
process.exit(failures ? 1 : 0);
