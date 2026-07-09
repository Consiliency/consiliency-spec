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
 *
 * Each vector's `input` is re-serialized with JSON.stringify (ASCII-safe for the corpus's
 * lone-surrogate ESCAPE, which serde_json rejects at parse — matching the reference). Note: this is
 * the corpus/vector path. The SEPARATE lone-surrogate BOUNDARY finding (wasm_surrogate_finding.mjs)
 * passes a REAL surrogate code unit across the &str boundary — a different, security-relevant path.
 */
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

const [glueDir, corpusPath] = process.argv.slice(2);
if (!glueDir || !corpusPath) {
  console.error("usage: node emit_wasm.mjs <glue_dir> <corpus.json>");
  process.exit(2);
}

const glue = await import(pathToFileURL(path.join(glueDir, "canon_core.js")).href);
const { canonicalBytesFromJson, digestFromJson } = glue;

const vectors = JSON.parse(readFileSync(corpusPath, "utf8"));

function runVector(vec) {
  const taggedJson = JSON.stringify(vec.input);
  if (vec.expect_error) {
    try {
      canonicalBytesFromJson(taggedJson);
    } catch {
      return ["ERROR", "ERROR"];
    }
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
