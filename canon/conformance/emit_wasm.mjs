/**
 * Emit canon output through the BUILT WASM binding (wasm-bindgen nodejs glue), not the TS reference.
 *
 * The XG4 gate's [5/5] step used to only `cargo check` the WASM surface — it proved the binding
 * COMPILES, never that the built `.wasm` a Node consumer loads emits the SAME bytes/digest as the
 * reference. This harness closes that gap: it loads the wasm-bindgen-generated module (the real
 * artifact) and emits one TAB-separated line per vector — `name\tbytes_b64\tdigest` sorted by name,
 * `ERROR\tERROR` for reject vectors — byte-identical in format to `py/test_canon.py --emit` and
 * `ts/canon.test.ts --emit` so the gate can `diff` them directly.
 *
 * Usage:  node emit_wasm.mjs <glue_dir> <corpus.json>
 *         node emit_wasm.mjs <glue_dir> --boundary <engine_boundary_vectors.json>
 *
 * `--boundary` emits `name\tOK|ERROR\tbytes_b64\tdigest` (`-\t-` on rejection), byte-identical in format
 * to `py/test_canon.py --emit-boundary`, for XG4's five-engine boundary diff.
 *
 * Only the binding's own error counts as a rejection: wasm-bindgen throws a CanonError's message as a
 * JS STRING. Any other throw (a TypeError, a RuntimeError from a trap) is a real failure (CAN-15).
 * Bare numbers are forwarded with their exact source spelling (JSON.rawJSON), so the engine sees
 * 9007199254740993 rather than the double JSON.parse would round it to.
 *
 * Each vector's `input` is re-serialized with JSON.stringify (ASCII-safe for the corpus's
 * lone-surrogate ESCAPE, which serde_json rejects at parse — matching the reference). Note: this is
 * the corpus/vector path. The SEPARATE lone-surrogate BOUNDARY finding (wasm_surrogate_finding.mjs)
 * passes a REAL surrogate code unit across the &str boundary — a different, security-relevant path.
 */
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const boundaryMode = args[1] === "--boundary";
const glueDir = args[0];
const corpusPath = boundaryMode ? args[2] : args[1];
if (!glueDir || !corpusPath) {
  console.error("usage: node emit_wasm.mjs <glue_dir> <corpus.json> | <glue_dir> --boundary <boundary.json>");
  process.exit(2);
}

const glue = await import(pathToFileURL(path.join(glueDir, "canon_core.js")).href);
const { canonicalBytesFromJson, digestFromJson } = glue;

// Keep each number's exact source text: JSON.stringify writes a JSON.rawJSON value back verbatim.
const exactNumbers = (_key, value, context) =>
  typeof value === "number" ? JSON.rawJSON(context.source) : value;
const vectors = JSON.parse(readFileSync(corpusPath, "utf8"), exactNumbers);

function rejected(fn) {
  try {
    fn();
  } catch (e) {
    if (typeof e === "string") return true; // the binding's CanonError message
    throw e;
  }
  return false;
}

if (boundaryMode) {
  const lines = vectors.map((bv) => {
    const tagged = Object.prototype.hasOwnProperty.call(bv, "raw") ? bv.raw : JSON.stringify(bv.input);
    let out;
    if (rejected(() => { out = canonicalBytesFromJson(tagged); })) return `${bv.name}\tERROR\t-\t-`;
    return `${bv.name}\tOK\t${Buffer.from(out).toString("base64")}\t${digestFromJson(tagged, "semantic-content")}`;
  });
  process.stdout.write(lines.sort().join("\n") + "\n");
  process.exit(0);
}

function runVector(vec) {
  const taggedJson = JSON.stringify(vec.input);
  if (vec.expect_error) {
    if (rejected(() => canonicalBytesFromJson(taggedJson))) return ["ERROR", "ERROR"];
    throw new Error(`vector ${vec.name}: expected CanonError but WASM binding accepted it`);
  }
  const b64 = Buffer.from(canonicalBytesFromJson(taggedJson)).toString("base64");
  const dig = digestFromJson(taggedJson, vec.profile);
  return [b64, dig];
}

const lines = [];
for (const vec of vectors) {
  const [b64, dig] = runVector(vec);
  lines.push(`${vec.name}\t${b64}\t${dig}`);
}
lines.sort();
process.stdout.write(lines.join("\n") + "\n");
