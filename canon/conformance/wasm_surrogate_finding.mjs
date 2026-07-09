/**
 * SECURITY BOUNDARY FINDING — the WASM `&str` surface is LOSSY on lone surrogates (SPEC §5.0).
 *
 * SPEC §5.0 exists to kill exactly one class of silent divergence: a lone (unpaired) UTF-16
 * surrogate that Python RAISES on and JS would emit as U+FFFD. canon's Python + TypeScript ports
 * REJECT it. The Rust core rejects it too — BUT only for input that actually reaches Rust as a
 * surrogate.
 *
 * wasm-bindgen marshals a JS string into a Rust `&str` via TextEncoder semantics, which replace any
 * lone surrogate with U+FFFD (0xEF 0xBF 0xBD) BEFORE Rust sees it. So a caller who hands the built
 * WASM binding a JS string containing a REAL lone-surrogate code unit gets it SILENTLY canonicalized
 * as U+FFFD — the WASM surface ACCEPTS and encodes what the enforcing ports REJECT. This is invisible
 * to `cargo check` and to the corpus vectors (which store the surrogate as an ASCII \uXXXX escape
 * that serde_json rejects at parse — a different path). It is only observable by driving a real
 * surrogate through the built artifact, which is what this harness does.
 *
 * This is a REPORT-not-fake situation. The finding is the justification for the design's decision to
 * put the dependency-free pure-JS v2 port (canon/ts/canon.ts) on the enforcing hot path and keep WASM
 * as a cross-language cross-check only. This harness therefore:
 *   (1) HARD-ASSERTS the enforcing paths (the pure-JS v2 port AND Python) REJECT a real lone
 *       surrogate — this is the load-bearing invariant and MUST stay true;
 *   (2) probes the built WASM binding and reports its boundary behavior LOUDLY. Silent-accept
 *       (U+FFFD) is the documented, expected finding and does NOT fail the gate (WASM is not the
 *       enforcer); it is recorded so any future drift (e.g. someone promoting WASM to the hot path,
 *       or the boundary behavior changing) is caught and re-examined.
 *
 * Usage:  node wasm_surrogate_finding.mjs <wasm_glue_dir> <ts_canon_path>
 * Exit 0 = enforcing paths reject (finding surfaced). Exit 1 = an enforcing path FAILED to reject.
 */
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import path from "node:path";

const [glueDir, tsCanonPath] = process.argv.slice(2);
if (!glueDir || !tsCanonPath) {
  console.error("usage: node wasm_surrogate_finding.mjs <wasm_glue_dir> <ts_canon_path>");
  process.exit(2);
}

// A REAL lone high surrogate (U+D800) as a code unit inside the JS string — NOT the \uXXXX escape.
const LONE = "\uD800";
const hasLoneSurrogate = [...`{"x":"${LONE}"}`].some((c) => c.codePointAt(0) === 0xd800);
if (!hasLoneSurrogate) {
  console.error("harness bug: test string does not contain a lone surrogate code unit");
  process.exit(2);
}

let failed = false;

// --- (1a) Enforcing path A — the pure-JS v2 port (canon/ts/canon.ts) MUST reject. ---
const ts = await import(pathToFileURL(tsCanonPath).href);
let tsRejected = false;
try {
  ts.canonicalBytes(LONE);
} catch {
  tsRejected = true;
}
console.log(`[enforcing] pure-JS v2 port (canon.ts): ${tsRejected ? "REJECTS ✓" : "ACCEPTS ✗"}`);
if (!tsRejected) failed = true;

// --- (1b) Enforcing path B — Python canon MUST reject. ---
const specRoot = path.dirname(path.dirname(tsCanonPath)); // <root>/canon
const pyProbe = [
  "import importlib.util,os",
  `spec=importlib.util.spec_from_file_location('c', os.path.join(${JSON.stringify(specRoot)},'py','canon.py'))`,
  "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)",
  "import sys",
  "try:\n m.canonical_bytes('\\ud800'); print('ACCEPTS')\nexcept Exception:\n print('REJECTS')",
].join("\n");
const py = spawnSync("python3", ["-c", pyProbe], { encoding: "utf8" });
const pyRejected = (py.stdout || "").trim().endsWith("REJECTS");
console.log(`[enforcing] Python canon: ${pyRejected ? "REJECTS ✓" : "ACCEPTS ✗"}`);
if (!pyRejected) failed = true;

// --- (2) The WASM binding — probe and REPORT its boundary behavior (expected: silent U+FFFD). ---
const glue = await import(pathToFileURL(path.join(glueDir, "canon_core.js")).href);
let wasmResult;
try {
  const out = glue.canonicalBytesFromJson(`{"$str":"${LONE}"}`);
  const hex = Buffer.from(out).toString("hex");
  const isReplacement = hex.includes("efbfbd");
  wasmResult = `ACCEPTS (encoded ${Buffer.from(out).toString()} | hex ${hex}${isReplacement ? "; lone surrogate → U+FFFD" : ""})`;
} catch (error) {
  wasmResult = `REJECTS (${String(error).slice(0, 80)})`;
}
console.log(`[cross-check] built WASM binding &str boundary: ${wasmResult}`);

console.log("");
console.log("================================ FINDING (SPEC §5.0) ================================");
if (wasmResult.startsWith("ACCEPTS")) {
  console.log("The built WASM binding SILENTLY ACCEPTS a real lone surrogate at the &str boundary");
  console.log("(wasm-bindgen replaces it with U+FFFD before Rust runs), while the enforcing pure-JS");
  console.log("v2 port and Python REJECT it. The WASM surface is therefore NOT safe for UNTRUSTED");
  console.log("input; the enforcing canonicalizer on the hot path MUST be the pure-JS v2 port (or");
  console.log("Python), never the WASM binding. WASM is retained as a cross-language cross-check only.");
} else {
  console.log("The built WASM binding rejected a real lone surrogate at the &str boundary. This is");
  console.log("SAFER than the documented wasm-bindgen behavior — re-examine before relying on it, and");
  console.log("update this finding if the boundary is genuinely enforcing across wasm-bindgen versions.");
}
console.log("====================================================================================");

if (failed) {
  console.error("\nENFORCING-PATH FAILURE: a hot-path canonicalizer did NOT reject a lone surrogate.");
  process.exit(1);
}
process.exit(0);
